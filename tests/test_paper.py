from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest

from investbell.paper import Ledger, Policy, SafetyStop, initialize, reconcile, step, stop, status

UTC = timezone.utc
NOW = datetime(2026, 9, 23, 14, 0, tzinfo=UTC)


class FakeBroker:
    def __init__(self):
        self.cash = 100000.
        self.qty = 0.
        self.orders = {}
        self.submissions = 0
        self.now = NOW
        self.price = 100.
        self.fill_fraction = 1
        self.ambiguous = False
        self.disappear = False
        self.account_id = 'paper-123'
        self.feed = 'sip'
        self.quote_age = 0
        self.is_open = True

    def account(self):
        return {'id': self.account_id, 'status': 'ACTIVE', 'currency': 'USD', 'cash': self.cash,
                'equity': self.cash+self.qty*self.price, 'buying_power': self.cash*4,
                'trading_blocked': False, 'account_blocked': False}

    def positions(self):
        return [{'symbol': 'SPY', 'qty': self.qty, 'market_value': self.qty*self.price}] if self.qty else []

    def list_orders(self, status='all'):
        return [o for o in self.orders.values() if status != 'open' or o['status'] not in {'filled', 'canceled', 'rejected', 'expired'}]

    def get_order_by_client_id(self, cid):
        return self.orders.get(cid)

    def submit_order(self, client_order_id, symbol, side, qty, limit_price, **kwargs):
        kwargs.get('pre_submit', lambda: None)()
        self.submissions += 1
        filled = qty*self.fill_fraction
        order = {'id': str(self.submissions), 'client_order_id': client_order_id, 'symbol': symbol,
                 'side': side, 'qty': qty, 'type': 'limit', 'time_in_force': 'day', 'limit_price': limit_price, 'filled_qty': filled, 'filled_avg_price': self.price if filled else None,
                 'status': 'filled' if filled == qty else 'partially_filled' if filled else 'new'}
        if not self.disappear:
            self.orders[client_order_id] = order
            self.cash += filled*self.price*(1 if side == 'sell' else -1)
            self.qty += filled*(-1 if side == 'sell' else 1)
        if self.ambiguous or self.disappear:
            raise TimeoutError('sensitive transport details')
        return order

    def cancel_order(self, order_id):
        for o in self.orders.values():
            if o['id'] == order_id:
                o['status'] = 'canceled'

    def clock(self):
        return {'timestamp': self.now.isoformat(), 'is_open': self.is_open}

    def calendar(self, start, end):
        return [{'date': start, 'open': '09:30', 'close': '16:00'}]

    def daily_bar(self, symbol, session):
        return {'timestamp': session+'T13:30:00+00:00', 'open': self.price}

    def latest_quote(self, symbol):
        return {'symbol': symbol, 'timestamp': (self.now-timedelta(seconds=self.quote_age)).isoformat(),
                'bid_price': self.price, 'ask_price': self.price, 'bid_size': 100, 'ask_size': 100, 'feed': self.feed}


class PaperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'paper.sqlite3'
        self.ledger = Ledger(self.path)
        self.addCleanup(lambda: self.ledger.db.close())
        self.broker = FakeBroker()
        initialize(self.ledger, self.broker, Policy(), now=NOW-timedelta(days=1))

    def freeze_test_policy(self, state):
        from investbell.paper import canonical_digest
        state['evidence_plan']['policy'] = state['policy']
        state['evidence_plan']['plan_sha256'] = canonical_digest({k:v for k,v in state['evidence_plan'].items() if k != 'plan_sha256'})
        self.ledger.save(state)

    def tick(self, seconds=0):
        self.broker.now = NOW+timedelta(seconds=seconds)
        return step(self.ledger, self.broker, now=self.broker.now)

    def test_real_cash_allocation_does_not_use_margin_or_full_account(self):
        self.assertEqual(self.tick(), 'submitted_buy')
        order = next(iter(self.broker.orders.values()))
        self.assertLessEqual(order['qty']*100.1, 200.001)
        self.assertEqual(self.ledger.state()['reserve_cash'], 90000)
        self.assertEqual(self.tick(15), 'session_complete')
        self.assertAlmostEqual(self.ledger.state()['expected_cash'], self.broker.cash)
        self.assertAlmostEqual(self.ledger.state()['target'], order['filled_qty']*100)

    def test_restart_and_repeat_do_not_duplicate_order_or_fill(self):
        self.tick()
        self.ledger.db.close()
        self.ledger = Ledger(self.path)
        self.tick(15)
        target = self.ledger.state()['target']
        self.tick(30)
        self.assertEqual(self.broker.submissions, 1)
        self.assertEqual(self.ledger.state()['target'], target)

    def test_ambiguous_submission_reconciles_without_resending(self):
        self.broker.ambiguous = True
        with self.assertRaisesRegex(SafetyStop, 'ambiguous'):
            self.tick()
        self.assertEqual(self.broker.submissions, 1)
        self.assertTrue(self.ledger.state()['halted'])
        with self.assertRaises(SafetyStop):
            self.tick(15)
        self.assertEqual(self.broker.submissions, 1)
        from investbell.paper import reconcile
        reconcile(self.ledger, self.broker)
        self.assertAlmostEqual(self.ledger.state()['expected_qty'], self.broker.qty)

    def test_unknown_submission_never_retries(self):
        self.broker.disappear = True
        with self.assertRaises(SafetyStop):
            self.tick()
        from investbell.paper import reconcile
        with self.assertRaisesRegex(SafetyStop, 'Unresolved submitted'):
            reconcile(self.ledger, self.broker)
        self.assertEqual(self.broker.submissions, 1)

    def test_partial_fills_are_applied_once_and_prevent_second_leg(self):
        self.broker.fill_fraction = .5
        self.tick()
        self.assertEqual(self.tick(15), 'awaiting_fill')
        state = self.ledger.state()
        self.assertAlmostEqual(state['expected_qty'], self.broker.qty)
        self.assertEqual(self.tick(30), 'awaiting_fill')
        self.assertEqual(self.ledger.state()['target'], state['target'])
        self.assertEqual(self.broker.submissions, 1)

    def test_partial_timeout_cancels_and_halts(self):
        self.broker.fill_fraction = .5
        self.tick()
        with self.assertRaisesRegex(SafetyStop, 'timed out'):
            self.tick(121)
        self.assertEqual(next(iter(self.broker.orders.values()))['status'], 'canceled')
        self.assertTrue(self.ledger.state()['halted'])

    def test_cash_mismatch_blocks_before_submit(self):
        self.broker.cash += 50
        with self.assertRaisesRegex(SafetyStop, 'cash does not reconcile'):
            self.tick()
        self.assertEqual(self.broker.submissions, 0)

    def test_account_identity_mismatch_blocks(self):
        self.broker.account_id = 'different'
        with self.assertRaisesRegex(SafetyStop, 'identity changed'):
            self.tick()
        self.assertEqual(self.broker.submissions, 0)

    def test_external_shares_block(self):
        self.broker.qty = 1
        with self.assertRaisesRegex(SafetyStop, 'shares do not reconcile'):
            self.tick()

    def test_stale_quote_blocks_before_submit(self):
        self.broker.quote_age = 30
        with self.assertRaisesRegex(SafetyStop, 'stale'):
            self.tick()
        self.assertEqual(self.broker.submissions, 0)

    def test_stale_quote_cancels_resting_order(self):
        self.broker.fill_fraction = 0
        self.tick()
        self.broker.quote_age = 30
        with self.assertRaisesRegex(SafetyStop, 'stale'):
            self.tick(15)
        self.assertEqual(next(iter(self.broker.orders.values()))['status'], 'canceled')

    def test_stop_persists_across_restart_and_cancels(self):
        self.broker.fill_fraction = 0
        self.tick()
        stop(self.ledger, self.broker)
        self.ledger.db.close()
        self.ledger = Ledger(self.path)
        with self.assertRaisesRegex(SafetyStop, 'Emergency stop'):
            self.tick(15)
        self.assertEqual(self.broker.submissions, 1)
        self.assertEqual(next(iter(self.broker.orders.values()))['status'], 'canceled')

    def test_notional_limit_halts_before_submit(self):
        state = self.ledger.state()
        state['policy']['max_order_notional'] = 100
        self.freeze_test_policy(state)
        with self.assertRaisesRegex(SafetyStop, 'notional'):
            self.tick()
        self.assertEqual(self.broker.submissions, 0)

    def test_missed_session_blocks_new_trades(self):
        self.tick()
        self.tick(15)
        self.broker.now = datetime(2026, 9, 25, 14, tzinfo=UTC)
        with self.assertRaisesRegex(SafetyStop, 'missed'):
            step(self.ledger, self.broker, now=self.broker.now)
        self.assertEqual(self.broker.submissions, 1)

    def test_closed_market_no_orders_and_heartbeat(self):
        self.broker.is_open = False
        self.assertEqual(self.tick(), 'market_closed')
        self.assertEqual(self.broker.submissions, 0)
        self.assertTrue(status(self.ledger, now=NOW)['healthy'])
        self.assertFalse(status(self.ledger, now=NOW+timedelta(seconds=181))['healthy'])

    def test_policy_invalid_limits(self):
        for params in ({'symbol':'SPXL'}, {'feed':'unknown'}, {'max_drawdown_pct':float('nan')}, {'limit_offset_bps':-1},
                       {'no_loss_sales': 'true'}, {'no_loss_sales': 1}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                Policy(**params)

    def test_fully_filled_exit_reentry_uses_actual_cash_same_session(self):
        state = self.ledger.state()
        state['policy'].update(dca_pct=50, max_daily_loss_pct=30, max_drawdown_pct=30, max_position_value=30000, max_order_notional=30000, max_daily_notional=60000)
        self.freeze_test_policy(state)
        self.tick()
        self.tick(15)
        for day,price in [(24,200),(25,180)]:
            self.broker.now = datetime(2026,9,day,14,tzinfo=UTC)
            self.broker.price = price
            step(self.ledger,self.broker,now=self.broker.now)
            self.broker.now += timedelta(seconds=15)
            step(self.ledger,self.broker,now=self.broker.now)
        state = self.ledger.state()
        self.assertTrue(state['capture'])
        self.assertTrue(state['sell_done'])
        orders = list(self.broker.orders.values())
        self.assertEqual([o['side'] for o in orders], ['buy','buy','sell','buy'])
        sell, buy = orders[-2:]
        self.assertLessEqual(buy['qty']*180.18, state['cycle_start']*.5+.001)
        self.assertEqual(sell['status'], 'filled')
        # The exit cleared the cost basis; only the re-entry purchase remains.
        reconcile(self.ledger, self.broker)
        self.assertAlmostEqual(self.ledger.state()['cost_basis'], buy['filled_qty']*180)

    def loosen(self, **changes):
        state = self.ledger.state()
        state['policy'].update(dca_pct=50, max_daily_loss_pct=60, max_drawdown_pct=60, max_position_value=30000,
                               max_order_notional=30000, max_daily_notional=60000, **changes)
        self.freeze_test_policy(state)

    def session(self, day, price):
        self.broker.now = datetime(2026, 9, day, 14, tzinfo=UTC)
        self.broker.price = price
        results = [step(self.ledger, self.broker, now=self.broker.now)]
        self.broker.now += timedelta(seconds=15)
        results.append(step(self.ledger, self.broker, now=self.broker.now))
        return results

    def bought(self):
        return sum(o['filled_qty']*o['filled_avg_price'] for o in self.broker.orders.values() if o['side'] == 'buy')

    def events(self):
        return [row['message'] for row in self.ledger.db.execute("SELECT message FROM events WHERE level='warning'")]

    def test_no_loss_rule_holds_capture_exit_after_gap_below_average_cost(self):
        self.loosen()
        self.session(23, 100)
        self.session(24, 200)
        reconcile(self.ledger, self.broker)
        basis = self.ledger.state()['cost_basis']
        self.assertAlmostEqual(basis, self.bought())
        # The 200 open signals a capture; the next session opens at 120, below the ~133 average cost.
        self.assertEqual(self.session(25, 120), ['held_sale', 'submitted_buy'])
        reconcile(self.ledger, self.broker)
        state = self.ledger.state()
        self.assertTrue(state['capture'])
        self.assertEqual(state['held_sales'], 1)
        self.assertEqual([o['side'] for o in self.broker.orders.values()], ['buy', 'buy', 'buy'])
        self.assertAlmostEqual(state['cost_basis'], self.bought())
        self.assertAlmostEqual(self.broker.qty, state['expected_qty'])
        self.assertRegex(self.events()[-1], r'^No-loss rule held a capture exit of .* below their cost')
        # Observations record the basis; the last one precedes the small DCA buy's fill.
        observation = json.loads(self.ledger.db.execute('SELECT payload FROM observations ORDER BY id DESC').fetchone()[0])
        self.assertAlmostEqual(observation['cost_basis'], basis)

    def test_capture_sells_below_cost_when_no_loss_rule_is_off(self):
        self.loosen(no_loss_sales=False)
        self.session(23, 100)
        self.session(24, 200)
        self.assertEqual(self.session(25, 120), ['submitted_sell', 'submitted_buy'])
        sell, rebuy = list(self.broker.orders.values())[-2:]
        self.assertLess(sell['filled_qty']*sell['filled_avg_price'], self.bought() - rebuy['filled_qty']*120)
        reconcile(self.ledger, self.broker)
        self.assertEqual(self.ledger.state()['held_sales'], 0)
        self.assertAlmostEqual(self.ledger.state()['cost_basis'], rebuy['filled_qty']*120)

    def test_no_loss_rule_holds_va_trim_but_allows_trim_above_cost(self):
        self.loosen(capture_pct=1000)
        self.session(23, 100)
        self.session(24, 150)
        self.assertEqual(self.session(25, 110)[0], 'held_sale')
        self.assertRegex(self.events()[-1], r'^No-loss rule held a VA trim')

        self.tearDownLedger()
        self.loosen(capture_pct=1000)
        self.session(23, 100)
        self.session(24, 150)
        reconcile(self.ledger, self.broker)
        before = self.ledger.state()
        self.assertEqual(self.session(25, 160), ['submitted_sell', 'submitted_buy'])
        reconcile(self.ledger, self.broker)
        sell, rebuy = list(self.broker.orders.values())[-2:]
        state = self.ledger.state()
        remaining = before['expected_qty'] - sell['filled_qty']
        self.assertGreater(remaining, 0)
        self.assertGreater(sell['filled_qty']*160, before['cost_basis']*sell['filled_qty']/before['expected_qty'])
        self.assertAlmostEqual(state['cost_basis'], before['cost_basis']*remaining/before['expected_qty'] + rebuy['filled_qty']*160)
        self.assertEqual(state['held_sales'], 0)

    def tearDownLedger(self):
        self.ledger.db.close()
        self.path.unlink()
        self.ledger = Ledger(self.path)
        self.broker = FakeBroker()
        initialize(self.ledger, self.broker, Policy(), now=NOW-timedelta(days=1))
