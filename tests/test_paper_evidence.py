from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from investbell.calendar import _calendar, expected_sessions
from investbell.paper import Policy
from investbell.paper_evidence import build_report, read_journal, review_journal
from investbell.research import canonical_digest


class PaperEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2025, 1, 2, 22, tzinfo=timezone.utc)
        self.sessions = expected_sessions("2024-01-02", "2025-01-01")[:252]
        policy = asdict(Policy(max_position_value=20000, max_drawdown_pct=50, max_daily_loss_pct=20))
        plan = {"created_at": "2024-01-01T20:00:00+00:00", "first_session": self.sessions[0],
                "min_sessions": 252, "min_end_session": self.sessions[-1],
                "policy": policy, "execution_sha256": "frozen"}
        plan["plan_sha256"] = canonical_digest(plan)
        self.journal = {"state": {"evidence_plan": plan, "policy": policy,
                                   "execution_sha256": "frozen", "halted": None},
                        "orders": [], "events": [], "observations": []}
        calendar = _calendar(2024, 2024)
        for i, session in enumerate(self.sessions):
            opened = calendar.session_open(session).to_pydatetime()
            for offset, phase in ((1, "before_order"), (2, "session_complete")):
                observed = opened + timedelta(minutes=offset)
                held = i > 0 or offset == 2
                payload = {"session": session, "reference_open": 100, "bid": 100, "ask": 100.01,
                           "cash": 9900 if held else 10000, "shares": 1 if held else 0,
                           "equity": 10000, "target": 100, "cycle_start": 10000,
                           "feed": "sip", "quote_timestamp": observed.isoformat(),
                           "execution_sha256": "frozen", "plan_sha256": plan["plan_sha256"],
                           "policy_sha256": canonical_digest(policy), "phase": phase}
                self.journal["observations"].append({"id": len(self.journal["observations"]) + 1,
                                                     "timestamp": observed.isoformat(), "payload": json.dumps(payload)})
        created = calendar.session_open(self.sessions[0]).to_pydatetime() + timedelta(minutes=1)
        self.journal["orders"].append({"client_id": "one", "session": self.sessions[0], "leg": "buy",
            "payload": json.dumps({"client_order_id": "one", "symbol": "SPY", "side": "buy", "qty": 1,
                                   "limit_price": 100, "risk_notional": 100}),
            "broker_id": "broker-one", "status": "filled", "filled_qty": 1, "filled_value": 100,
            "created": created.isoformat()})

    def review(self, journal=None, now=None):
        return review_journal(journal or self.journal, now=now or self.now, current_execution_sha256="frozen")

    def test_complete_recorded_window_still_requires_review_and_never_live_approval(self):
        result = self.review()
        self.assertEqual(result["status"], "review_required")
        self.assertFalse(result["live_ready"])
        self.assertEqual(result["observed_sessions"], 252)
        self.assertEqual(result["strategy"]["final_recorded_equity"], 10000)
        self.assertEqual(result["strategy"]["total_return_pct"], 0)
        self.assertIn("buy_and_hold", result["modeled_reference_open_benchmarks"])
        self.assertIn("Different valuation timestamps", result["comparison_qualification"])
        self.assertFalse(result["operational_audit"]["policy_breaches"])

    def test_held_sales_and_worst_unsold_loss_are_reported_without_failing_review(self):
        result = self.review()
        self.assertEqual((result["strategy"]["held_sales"], result["strategy"]["worst_unrealized_loss_pct"]), (0, None))
        self.assertTrue(result["strategy"]["no_loss_sales"])
        journal = deepcopy(self.journal)
        for index, row in enumerate(journal["observations"]):
            payload = json.loads(row["payload"])
            # One share bid at 100: cost 125 is a 20% unsold loss, 90 is a gain.
            payload["cost_basis"] = 125 if index == 40 else 90 if payload["shares"] else 0
            row["payload"] = json.dumps(payload)
        journal["events"].append({"id": 1, "timestamp": "2024-06-03T15:00:00+00:00", "level": "warning",
                                  "message": "No-loss rule held a VA trim of 0.500000000 shares: limit proceeds 49.95 are below their cost 62.50."})
        result = self.review(journal)
        self.assertEqual(result["status"], "review_required")
        self.assertEqual(result["strategy"]["held_sales"], 1)
        self.assertEqual(len(result["operational_audit"]["no_loss_held_sales"]), 1)
        self.assertAlmostEqual(result["strategy"]["worst_unrealized_loss_pct"], 20)
        payload = json.loads(journal["observations"][40]["payload"])
        payload["cost_basis"] = -1
        journal["observations"][40]["payload"] = json.dumps(payload)
        self.assertIn("Negative recorded cost basis.", [m["reason"] for m in self.review(journal)["malformed_observations"]])

    def test_time_cannot_be_replaced_by_preloaded_future_observations(self):
        result = self.review(now=datetime(2024, 1, 3, 22, tzinfo=timezone.utc))
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertFalse(result["window"]["elapsed"])
        self.assertTrue(result["malformed_observations"])

    def test_missing_interior_or_first_session_cannot_be_replaced_by_later_days(self):
        for removed in (self.sessions[0], self.sessions[60]):
            journal = deepcopy(self.journal)
            journal["observations"] = [row for row in journal["observations"] if json.loads(row["payload"])["session"] != removed]
            result = self.review(journal)
            self.assertEqual(result["status"], "insufficient_evidence")
            self.assertIn(removed, result["missing_sessions"])

    def test_historical_backfill_and_changed_fingerprints_do_not_qualify(self):
        changes = [("timestamp", "2025-01-01T15:00:00+00:00"),
                   ("policy_sha256", "changed"), ("execution_sha256", "changed"), ("plan_sha256", "changed")]
        for key, value in changes:
            journal = deepcopy(self.journal)
            if key == "timestamp":
                journal["observations"][0][key] = value
            else:
                payload = json.loads(journal["observations"][0]["payload"])
                payload[key] = value
                journal["observations"][0]["payload"] = json.dumps(payload)
            with self.subTest(key=key):
                result = self.review(journal)
                self.assertEqual(result["status"], "insufficient_evidence")
                self.assertTrue(result["malformed_observations"])

    def test_late_start_and_no_completed_phase_are_insufficient(self):
        for missing_phase in (False, True):
            journal = deepcopy(self.journal)
            if missing_phase:
                journal["observations"] = [row for row in journal["observations"] if json.loads(row["payload"])["phase"] != "session_complete"]
            else:
                for row in journal["observations"][:2]:
                    later = datetime.fromisoformat(row["timestamp"]) + timedelta(minutes=20)
                    row["timestamp"] = later.isoformat()
                    payload = json.loads(row["payload"])
                    payload["quote_timestamp"] = later.isoformat()
                    row["payload"] = json.dumps(payload)
            self.assertEqual(self.review(journal)["status"], "insufficient_evidence")

    def test_duplicate_orders_unresolved_fills_and_critical_events_fail_recorded_checks(self):
        for case in ("duplicate", "pending", "critical", "risk"):
            journal = deepcopy(self.journal)
            if case == "duplicate":
                journal["orders"].append(deepcopy(journal["orders"][0]))
            elif case == "pending":
                pending = deepcopy(journal["orders"][0])
                pending.update(client_id="pending", broker_id="broker-pending", status="accepted", filled_qty=0, filled_value=0)
                payload = json.loads(pending["payload"])
                payload["client_order_id"] = "pending"
                pending["payload"] = json.dumps(payload)
                journal["orders"].append(pending)
            elif case == "critical":
                journal["events"].append({"id": 1, "timestamp": "2024-06-03T15:00:00+00:00", "level": "critical", "message": "Cash does not reconcile"})
            else:
                payload = json.loads(journal["orders"][0]["payload"])
                payload["risk_notional"] = 100000
                journal["orders"][0]["payload"] = json.dumps(payload)
            with self.subTest(case=case):
                result = self.review(journal)
                self.assertEqual(result["status"], "failed_recorded_checks")
                self.assertFalse(result["live_ready"])

    def test_appreciation_can_exceed_purchase_cap_but_new_buy_cannot(self):
        journal = deepcopy(self.journal)
        policy = journal["state"]["policy"]
        policy["max_position_value"] = 100
        plan = journal["state"]["evidence_plan"]
        plan["policy"] = policy
        plan["plan_sha256"] = canonical_digest({key: value for key, value in plan.items() if key != "plan_sha256"})
        for row in journal["observations"]:
            payload = json.loads(row["payload"])
            payload["policy_sha256"] = canonical_digest(policy)
            payload["plan_sha256"] = plan["plan_sha256"]
            if payload["session"] > self.sessions[0]:
                payload.update(reference_open=110, bid=110, ask=110.01, equity=10010)
            row["payload"] = json.dumps(payload)
        result = self.review(journal)
        self.assertEqual(result["status"], "review_required")
        self.assertFalse(result["operational_audit"]["policy_breaches"])

        # First order used its exact-time pre-buy mark and fit the $100 cap.
        # A subsequent buy while the original holding has appreciated does not.
        next_order = deepcopy(journal["orders"][0])
        next_order.update(client_id="second", broker_id="broker-second", session=self.sessions[1],
                          created=journal["observations"][2]["timestamp"], filled_value=110)
        next_order["payload"] = json.dumps({"client_order_id": "second", "symbol": "SPY", "side": "buy",
                                           "qty": 1, "limit_price": 110, "risk_notional": 110})
        journal["orders"].append(next_order)
        result = self.review(journal)
        self.assertEqual(result["status"], "failed_recorded_checks")
        self.assertTrue(any("new buy" in item for item in result["operational_audit"]["policy_breaches"]))

    def test_buy_without_prior_contemporaneous_mark_is_insufficient(self):
        journal = deepcopy(self.journal)
        journal["observations"] = journal["observations"][1:]
        result = self.review(journal)
        self.assertEqual(result["status"], "insufficient_evidence")
        self.assertEqual(result["operational_audit"]["buy_intents_without_contemporaneous_marks"], ["one"])
        self.assertFalse(result["missing_sessions"])

    def test_missing_schema_and_missing_file_produce_insufficient_without_creating_ledger(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "paper.sqlite3"
            result = build_report(path, now=self.now)
            self.assertEqual(result["status"], "insufficient_evidence")
            self.assertFalse(path.exists())
            with sqlite3.connect(path) as conn:
                conn.execute("CREATE TABLE state(id INTEGER, payload TEXT)")
            result = build_report(path, now=self.now)
            self.assertIn("schema", result["issues"][0])

    def test_read_only_snapshot_preserves_database_and_reads_new_schema(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "paper.sqlite3"
            with sqlite3.connect(path) as conn:
                conn.executescript("""
                    CREATE TABLE state(id INTEGER,payload TEXT);
                    CREATE TABLE orders(client_id TEXT,created TEXT);
                    CREATE TABLE events(id INTEGER,timestamp TEXT,level TEXT,message TEXT);
                    CREATE TABLE observations(id INTEGER,timestamp TEXT,payload TEXT);
                """)
                conn.execute("INSERT INTO state VALUES(1,?)", (json.dumps(self.journal["state"]),))
                for row in self.journal["observations"]:
                    conn.execute("INSERT INTO observations VALUES(?,?,?)", (row["id"], row["timestamp"], row["payload"]))
            before = path.read_bytes()
            loaded = read_journal(path)
            self.assertEqual(len(loaded["observations"]), 504)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
