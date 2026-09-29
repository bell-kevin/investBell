"""Independent fault scenarios for the persistent paper account runner."""

from contextlib import redirect_stderr
from datetime import datetime, timedelta
import io
import http.client
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from investbell.paper import (Ledger, Policy, SafetyStop, acknowledge_cash,
                             canonical_digest, initialize, load_credentials,
                             read_status, reconcile, resume_after_review, step,
                             stop, status)
from tests.test_paper import FakeBroker, NOW, UTC


class PaperFaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ledger = Ledger(Path(self.tmp.name) / 'faults.sqlite3')
        self.addCleanup(self.ledger.db.close)
        self.stderr = io.StringIO()
        self.redirect = redirect_stderr(self.stderr)
        self.redirect.__enter__()
        self.addCleanup(self.redirect.__exit__, None, None, None)
        self.broker = FakeBroker()
        initialize(self.ledger, self.broker, Policy(), now=NOW-timedelta(days=1))

    def freeze_test_policy(self, state):
        state['evidence_plan']['policy'] = state['policy']
        state['evidence_plan']['plan_sha256'] = canonical_digest(
            {key: value for key, value in state['evidence_plan'].items() if key != 'plan_sha256'})
        self.ledger.save(state)

    def tick(self, seconds=0):
        self.broker.now = NOW + timedelta(seconds=seconds)
        return step(self.ledger, self.broker, now=self.broker.now)

    def test_daily_loss_cancels_resting_partially_filled_buy(self):
        state = self.ledger.state()
        state['policy']['dca_pct'] = 50
        self.freeze_test_policy(state)
        self.broker.fill_fraction = .5
        self.assertEqual(self.tick(), 'submitted_buy')
        self.broker.price = 80
        with self.assertRaisesRegex(SafetyStop, 'drawdown or daily-loss'):
            self.tick(15)
        order = next(iter(self.broker.orders.values()))
        self.assertEqual(order['status'], 'canceled')
        self.assertEqual(self.broker.submissions, 1)
        state = self.ledger.state()
        self.assertAlmostEqual(state['expected_qty'], self.broker.qty)
        self.assertAlmostEqual(state['expected_cash'], self.broker.cash)
        self.assertLess(state['last_equity'], state['day_start_equity'] * .97)

    def test_order_terms_tampering_cancels_and_never_submits_another_order(self):
        self.broker.fill_fraction = 0
        self.tick()
        order = next(iter(self.broker.orders.values()))
        for field, value in (('qty', order['qty'] + 1), ('limit_price', 150),
                             ('type', 'market'), ('time_in_force', 'gtc'),
                             ('replaced_by', 'another-order')):
            with self.subTest(field=field):
                original = dict(order)
                order[field] = value
                with self.assertRaisesRegex(SafetyStop, 'terms differ'):
                    self.tick(15)
                self.assertEqual(order['status'], 'canceled')
                self.assertEqual(self.broker.submissions, 1)
                order.clear()
                order.update(original)
                state = self.ledger.state()
                state['halted'] = None
                self.ledger.save(state)

    def test_canceled_partial_fill_requires_review_then_is_never_repeated(self):
        self.broker.fill_fraction = .5
        self.tick()
        order = next(iter(self.broker.orders.values()))
        self.broker.cancel_order(order['id'])
        with self.assertRaisesRegex(SafetyStop, 'reconciled any partial fill'):
            self.tick(15)
        reconciled = self.ledger.state()
        self.assertAlmostEqual(reconciled['expected_cash'], self.broker.cash)
        self.assertAlmostEqual(reconciled['expected_qty'], self.broker.qty)
        with self.assertRaisesRegex(SafetyStop, 'acknowledge-incomplete'):
            resume_after_review(self.ledger, self.broker, reason='Reviewed partial fill and broker cancellation.')
        with patch('investbell.calendar.latest_completed_session', return_value=NOW.date() - timedelta(days=1)):
            resume_after_review(self.ledger, self.broker, reason='Accept the partial day without resubmitting.',
                                acknowledge_incomplete=True)
        self.assertEqual(self.tick(30), 'session_complete')
        self.assertEqual(self.broker.submissions, 1)
        self.assertEqual(self.ledger.state()['target'], reconciled['target'])
        self.assertEqual(self.ledger.state()['expected_qty'], reconciled['expected_qty'])
        events = [row[0] for row in self.ledger.db.execute('SELECT message FROM events')]
        self.assertTrue(any('skipped incomplete session=True' in message for message in events))

    def test_pending_cancel_is_unconfirmed_and_cannot_resume(self):
        self.broker.fill_fraction = 0
        self.tick()
        order = next(iter(self.broker.orders.values()))
        self.broker.cancel_order = lambda _id: order.update(status='pending_cancel')
        self.assertFalse(stop(self.ledger, self.broker))
        self.assertTrue(self.ledger.stop_path.exists())
        self.assertFalse(status(self.ledger, now=NOW)['healthy'])
        with self.assertRaisesRegex(SafetyStop, 'Unresolved orders'):
            resume_after_review(self.ledger, self.broker, reason='Cancellation was requested.')
        self.assertIn('Cancellation unresolved', self.stderr.getvalue())
        self.assertTrue(self.ledger.state()['halted'])
        self.assertEqual(self.broker.submissions, 1)

    def test_external_deposit_and_withdrawal_do_not_change_allocated_equity(self):
        initial = self.ledger.state()
        for delta in (500, -200):
            with self.subTest(delta=delta):
                self.broker.cash += delta
                acknowledge_cash(self.ledger, self.broker, kind='external', expected_delta=delta,
                                 reason='Verified transfer on the broker statement.')
                state = self.ledger.state()
                self.assertEqual(state['expected_cash'] - state['reserve_cash'], 10000)
                self.assertEqual(state['cycle_start'], initial['cycle_start'])
                self.assertTrue(state['halted'])
        events = [row[0] for row in self.ledger.db.execute('SELECT message FROM events')]
        self.assertTrue(any('external cash adjustment 500.00' in item for item in events))
        self.assertTrue(any('external cash adjustment -200.00' in item for item in events))

    def test_income_and_expense_affect_strategy_cash_but_require_explicit_resume(self):
        for kind, delta in (('income', 25), ('expense', -2)):
            with self.subTest(kind=kind):
                self.broker.cash += delta
                acknowledge_cash(self.ledger, self.broker, kind=kind, expected_delta=delta,
                                 reason='Verified dividend or fee in broker activity.')
                self.assertEqual(self.ledger.state()['reserve_cash'], 90000)
                self.assertTrue(self.ledger.state()['halted'])
        self.assertEqual(self.ledger.state()['expected_cash'] - self.ledger.state()['reserve_cash'], 10023)
        with self.assertRaisesRegex(SafetyStop, 'Cash event acknowledged'):
            self.tick()
        resume_after_review(self.ledger, self.broker, reason='Verified the classified cash entries.')
        self.assertFalse(self.ledger.state()['halted'])

    def test_cash_review_rejects_wrong_amount_wrong_sign_and_capital_withdrawal(self):
        baseline = self.ledger.state()
        self.broker.cash += 10
        with self.assertRaisesRegex(SafetyStop, 'reviewed amount'):
            acknowledge_cash(self.ledger, self.broker, kind='income', expected_delta=20, reason='Mismatch.')
        for kind, delta in (('income', -10), ('expense', 10)):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                acknowledge_cash(self.ledger, self.broker, kind=kind, expected_delta=delta, reason='Wrong sign.')
        self.broker.cash = 9000
        with self.assertRaisesRegex(SafetyStop, 'consumes strategy capital'):
            acknowledge_cash(self.ledger, self.broker, kind='external', expected_delta=-91000,
                             reason='Withdrawal exceeds unallocated reserve.')
        self.assertEqual(self.ledger.state(), baseline)

    def test_cash_adjustment_cannot_bypass_unresolved_orders(self):
        self.broker.fill_fraction = 0
        self.tick()
        self.broker.cash += 10
        with self.assertRaisesRegex(SafetyStop, 'all orders to finish'):
            acknowledge_cash(self.ledger, self.broker, kind='income', expected_delta=10,
                             reason='Verified income but an order is still open.')
        self.assertNotEqual(self.ledger.state()['expected_cash'], self.broker.cash)

    def test_stop_arriving_during_broker_preflight_vetoes_submission(self):
        submit = self.broker.submit_order
        def stop_then_submit(**kwargs):
            self.ledger.stop_path.write_text('Emergency stop raced with broker preflight.\n')
            return submit(**kwargs)
        self.broker.submit_order = stop_then_submit
        with self.assertRaisesRegex(SafetyStop, 'Submission failed'):
            self.tick()
        self.assertEqual(self.broker.submissions, 0)
        self.assertFalse(self.broker.orders)
        self.assertTrue(self.ledger.state()['halted'])
        self.assertEqual(self.ledger.db.execute('SELECT status FROM orders').fetchone()[0], 'rejected')
        self.assertTrue(self.ledger.stop_path.exists())

    def test_overnight_gap_counts_toward_daily_loss_before_next_buy(self):
        state = self.ledger.state()
        state['policy']['dca_pct'] = 50
        self.freeze_test_policy(state)
        self.tick()
        self.tick(15)
        prior_equity = self.ledger.state()['last_equity']
        self.broker.now = datetime(2026, 9, 24, 14, tzinfo=UTC)
        self.broker.price = 90
        with self.assertRaisesRegex(SafetyStop, 'drawdown or daily-loss'):
            step(self.ledger, self.broker, now=self.broker.now)
        state = self.ledger.state()
        self.assertEqual(state['day_start_equity'], prior_equity)
        self.assertLess(state['last_equity'], prior_equity * .97)
        self.assertGreater(state['last_equity'], state['peak'] * .90)
        self.assertEqual(self.broker.submissions, 1)

    def test_positive_partial_quantity_without_fill_price_halts_before_accounting(self):
        self.broker.fill_fraction = .5
        self.tick()
        order = next(iter(self.broker.orders.values()))
        order['filled_avg_price'] = None
        with self.assertRaisesRegex(SafetyStop, 'positive fill price'):
            self.tick(15)
        self.assertEqual(self.ledger.state()['expected_qty'], 0)
        self.assertEqual(order['status'], 'canceled')
        self.assertEqual(self.broker.submissions, 1)

    def test_failure_between_fill_watermark_and_state_write_rolls_back_both(self):
        self.tick()
        original_save = self.ledger.save
        failed = False
        def fail_first_save(state):
            nonlocal failed
            if not failed:
                failed = True
                raise OSError('Simulated interruption after fill watermark update.')
            return original_save(state)
        with patch.object(self.ledger, 'save', side_effect=fail_first_save):
            with self.assertRaises(SafetyStop):
                self.tick(15)
        row = self.ledger.db.execute('SELECT status,filled_qty FROM orders').fetchone()
        self.assertEqual(row['status'], 'submitting')
        self.assertEqual(row['filled_qty'], 0)
        self.assertEqual(self.ledger.state()['expected_qty'], 0)
        # Recovery must still see and apply the actual broker fill exactly once.
        reconcile(self.ledger, self.broker, reviewed=True)
        repaired = self.ledger.state()
        self.assertEqual(repaired['expected_qty'], self.broker.qty)
        self.assertEqual(repaired['expected_cash'], self.broker.cash)
        reconcile(self.ledger, self.broker, reviewed=True)
        self.assertEqual(self.ledger.state(), repaired)
        self.assertEqual(self.broker.submissions, 1)

    def test_registration_waits_for_next_full_session_before_any_order(self):
        today = Ledger(Path(self.tmp.name) / 'registered-today.sqlite3')
        self.addCleanup(today.db.close)
        initialized = initialize(today, self.broker, Policy(), now=NOW)
        self.assertEqual(initialized['evidence_plan']['first_session'], '2026-09-24')
        self.assertEqual(step(today, self.broker, now=NOW), 'awaiting_registered_start')
        self.assertEqual(self.broker.submissions, 0)
        self.assertEqual(today.db.execute('SELECT count(*) FROM orders').fetchone()[0], 0)
        self.assertEqual(today.db.execute('SELECT count(*) FROM observations').fetchone()[0], 0)
        self.broker.now = NOW+timedelta(days=1)
        self.assertEqual(step(today, self.broker, now=self.broker.now), 'submitted_buy')

    def test_missed_registered_first_session_cannot_backfill_or_begin_late(self):
        self.broker.now = NOW+timedelta(days=1)
        with self.assertRaisesRegex(SafetyStop, 'first paper session was missed'):
            step(self.ledger, self.broker, now=self.broker.now)
        self.assertEqual(self.broker.submissions, 0)
        self.assertIsNone(self.ledger.state()['session'])
        self.assertTrue(self.ledger.state()['halted'])

    def test_modified_policy_or_registered_plan_halts_before_submission(self):
        baseline = self.ledger.state()
        for mutation in ('policy', 'plan'):
            with self.subTest(mutation=mutation):
                state = json.loads(json.dumps(baseline))
                if mutation == 'policy':
                    state['policy']['dca_pct'] = 99
                else:
                    state['evidence_plan']['first_session'] = '2020-01-02'
                self.ledger.save(state)
                with self.assertRaisesRegex(SafetyStop, 'Frozen paper plan or policy changed'):
                    self.tick()
                self.assertTrue(self.ledger.state()['halted'])
                self.assertEqual(self.broker.submissions, 0)

    def test_changed_execution_code_blocks_step_and_resume(self):
        with patch('investbell.paper.execution_digest', return_value='changed-code'):
            with self.assertRaisesRegex(SafetyStop, 'Execution code changed'):
                self.tick()
            with self.assertRaisesRegex(SafetyStop, 'Code changed'):
                resume_after_review(self.ledger, self.broker, reason='A reason does not override a frozen experiment.')
        self.assertTrue(self.ledger.state()['halted'])
        self.assertEqual(self.broker.submissions, 0)

    def test_status_of_missing_ledger_does_not_create_database_or_parent_directory(self):
        missing = Path(self.tmp.name) / 'never-created' / 'paper.sqlite3'
        result = read_status(missing)
        self.assertFalse(result['initialized'])
        self.assertFalse(result['healthy'])
        self.assertFalse(missing.parent.exists())

    def test_status_of_existing_ledger_is_read_only_and_omits_account_state(self):
        self.tick()
        before = self.ledger.state()
        counts = {table: self.ledger.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                  for table in ('orders', 'events', 'observations')}
        result = read_status(self.ledger.path)
        self.assertTrue(result['initialized'])
        self.assertFalse({'account_id', 'expected_cash', 'expected_qty', 'orders'} & result.keys())
        self.assertEqual(before, self.ledger.state())
        self.assertEqual(counts, {table: self.ledger.db.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
                                 for table in counts})

    def test_credentials_reject_public_permissions_before_loading_any_value(self):
        path = Path(self.tmp.name) / '.env.paper'
        path.write_text('APCA_API_KEY_ID=example-paper-key\n')
        path.chmod(0o644)
        with patch.dict(os.environ, {'APCA_API_KEY_ID': 'existing-value'}):
            with self.assertRaisesRegex(ValueError, 'private'):
                load_credentials(path)
            self.assertEqual(os.environ['APCA_API_KEY_ID'], 'existing-value')

    def test_credentials_treat_shell_expansions_as_literal_text(self):
        path = Path(self.tmp.name) / '.env.paper'
        marker = Path(self.tmp.name) / 'shell-was-executed'
        key = '$(touch ' + str(marker) + ')'
        secret = '`touch ' + str(marker) + '`-${HOME}'
        path.write_text('APCA_API_KEY_ID="' + key + '"\nAPCA_API_SECRET_KEY=\'' + secret + '\'\n')
        path.chmod(0o600)
        with patch.dict(os.environ):
            load_credentials(path)
            self.assertEqual(os.environ['APCA_API_KEY_ID'], key)
            self.assertEqual(os.environ['APCA_API_SECRET_KEY'], secret)
        self.assertFalse(marker.exists())

    def test_dashboard_status_and_source_download_omit_credentials_and_private_ledger(self):
        from investbell.server import make_server
        root = Path(self.tmp.name) / 'download-fixture'
        root.mkdir()
        (root / 'README.md').write_text('Public research source.\n')
        (root / '.env.paper').write_text('APCA_API_SECRET_KEY=private-paper-secret\n')
        (root / 'data').mkdir()
        (root / 'data' / 'paper.sqlite3').write_text('private-account-ledger')
        server = make_server(port=0, data_dir=self.tmp.name)
        server.paper_ledger = self.ledger.path
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with patch('investbell.server.ROOT', root), patch.dict(os.environ, {'APCA_API_SECRET_KEY': 'private-paper-secret'}):
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
                try:
                    connection.request('GET', '/api/paper-status')
                    response = connection.getresponse()
                    body = response.read()
                    self.assertEqual(response.status, 503)
                    self.assertNotIn(b'private-paper-secret', body)
                    self.assertNotIn(self.broker.account_id.encode(), body)
                    connection.request('GET', '/api/source')
                    response = connection.getresponse()
                    self.assertEqual(response.status, 200)
                    with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
                        self.assertEqual(archive.namelist(), ['investBell/README.md'])
                        self.assertNotIn(b'private-paper-secret', archive.read('investBell/README.md'))
                finally:
                    connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
