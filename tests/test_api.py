import http.client
import io
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from unittest.mock import patch

from investbell import healthcheck
from investbell.daily import write_report
from investbell.data import DataUnavailable, demo_bars
from investbell.engine import Bar, Parameters
from investbell.research_execution import simulate_execution
from investbell.server import make_server
from investbell.service import run, validate_request


REQUEST = {"source": "demo", "symbol": "SPY", "start": "2024-01-01", "end": "2024-02-01",
           "grid": {"dca": [1, 2], "va": [0, 0.1], "capture": [5, 10]}}


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.server = make_server(port=0, data_dir=cls.temp.name)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def request(self, method, path, payload=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            connection.request(method, path, body=json.dumps(payload) if payload is not None else None,
                               headers=headers or {"Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_health_and_bounded_grid_result(self):
        status, health = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["paper_only"])
        status, result = self.request("POST", "/api/run", REQUEST)
        self.assertEqual(status, 200)
        self.assertEqual(result["source"]["kind"], "demo")
        self.assertEqual(len(result["grid"]), 8)
        self.assertEqual(result["bars_count"], len(result["selected"]["equity"]))
        self.assertIn("SYNTHETIC", result["source"]["label"])

    def test_container_health_probe_follows_dashboard(self):
        base = f"http://127.0.0.1:{self.server.server_port}"
        self.assertEqual(healthcheck.main(base + "/api/health"), 0)
        self.assertEqual(healthcheck.main(base + "/api/no-such-endpoint"), 1)

    def test_every_cube_is_compared_with_simple_dca_at_its_own_budget(self):
        result = run(REQUEST)
        baselines = {row["dca_pct"]: row["final_equity"] for row in result["comparison"]["simple_dca"]}
        self.assertEqual(set(baselines), {1.0, 2.0})
        bars = demo_bars(REQUEST["start"], REQUEST["end"])
        for dca, equity in baselines.items():
            expected = simulate_execution(bars, Parameters(dca_pct=dca), policy="simple_dca")["metrics"]["final_equity"]
            self.assertAlmostEqual(equity, expected, places=9)
        for cell in result["grid"]:
            self.assertEqual(cell["simple_dca_final_equity"], baselines[cell["dca_pct"]])
            self.assertAlmostEqual(cell["vs_simple_dca_pct"],
                                   (cell["final_equity"] / baselines[cell["dca_pct"]] - 1) * 100, places=9)
        selected = result["selected"]["metrics"]
        self.assertAlmostEqual(selected["vs_simple_dca_pct"],
                               (selected["final_equity"] / baselines[2.0] - 1) * 100, places=9)

    def test_simple_dca_comparison_is_zero_when_va_and_capture_never_trade(self):
        # A falling price never exceeds the VA target or the capture threshold.
        bars = [Bar(f"2024-01-{day:02d}", 100 - day) for day in range(2, 30)]
        store = type("Store", (), {"load": lambda self, *args, **kwargs: (bars, {"kind": "yahoo", "sha256": "x"})})()
        result = run({**REQUEST, "source": "yahoo", "start": "2024-01-02", "end": "2024-01-30"}, store=store)
        for cell in result["grid"]:
            self.assertAlmostEqual(cell["vs_simple_dca_pct"], 0.0, places=9)

    def test_invalid_parameters_rejected_before_download(self):
        with patch("investbell.data.fetch_yahoo") as fetch:
            status, body = self.request("POST", "/api/run", {**REQUEST, "dca_pct": float("nan")})
            self.assertEqual(status, 400)
            fetch.assert_not_called()
        # Leveraged sector funds and unknown tickers stay outside the research universe.
        for symbol in ("SOXL", "PENNY"):
            status, body = self.request("POST", "/api/run", {**REQUEST, "symbol": symbol})
            self.assertEqual(status, 400)
        status, body = self.request("POST", "/api/run", {**REQUEST, "grid": {"dca": list(range(1, 9)), "va": [0], "capture": [1]}})
        self.assertEqual(status, 400)

    def test_slide_requirements_and_user_clarification_are_exported(self):
        status, configuration = self.request("GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertEqual(configuration["model_version"], "dca-va-capture-v2")
        self.assertEqual(set(configuration["defaults"]["grid"]), {"dca", "va", "capture"})
        self.assertIn("same session", configuration["strategy"]["capture"])
        result = run(REQUEST)
        self.assertEqual(result["strategy"], configuration["strategy"])
        self.assertEqual(result["model_version"], configuration["model_version"])

    def test_full_exit_plan_includes_same_session_dca(self):
        observations = [Bar("2024-01-01", 100), Bar("2024-01-02", 200)]
        source = {"kind": "yahoo", "label": "test daily opens", "sha256": "fixture"}
        with patch.object(self.server.price_store, "load", return_value=(observations, source)):
            status, result = self.request("POST", "/api/run", {
                **REQUEST, "source": "yahoo", "initial_cash": 1000, "dca_pct": 50,
                "va_pct": 5, "capture_pct": 5, "slippage_bps": 0,
            })
        self.assertEqual(status, 200)
        plan = result["selected"]["next_action"]
        self.assertEqual(plan["action"], "capture")
        self.assertEqual(plan["sell_shares"], 7.5)
        self.assertEqual(plan["estimated_sell_net"], 1500)
        self.assertEqual(plan["estimated_buy_dollars"], 750)
        self.assertTrue(plan["buy_dollars_contingent_on_exit"])

    def test_yahoo_failure_is_visible_no_demo_fallback(self):
        with patch.object(self.server.price_store, "load", side_effect=DataUnavailable("Yahoo unavailable")):
            status, body = self.request("POST", "/api/run", {**REQUEST, "source": "yahoo"})
        self.assertEqual(status, 502)
        self.assertEqual(body["error"], "Yahoo unavailable")

    def test_foreign_origin_blocked(self):
        status, body = self.request("POST", "/api/run", REQUEST,
                                    {"Content-Type": "application/json", "Origin": "https://example.org"})
        self.assertEqual(status, 403)

    def test_source_download_contains_runnable_code_without_private_files(self):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            connection.request("GET", "/api/source")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(response.getheader("Content-Type"), "application/zip")
            with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
                names = archive.namelist()
                for expected in ("LICENSE", "requirements.txt", "investbell/engine.py", "static/cube.js", "scripts/start.sh",
                                 "Containerfile", "deploy/investbell-dashboard.container",
                                 "deploy/investbell-scheduler.container", "investbell/scheduler.py", "docs/server.md",
                                 "docs/strategy-requirements.md", "docs/paper-trading.md", "docs/validation.md",
                                 "docs/slide-audit.md", "investbell/training.py",
                                 "investbell/broker.py", "investbell/paper.py", "deploy/paper-policy.json"):
                    self.assertIn("investBell/" + expected, names)
                self.assertFalse(any(part in name for name in names for part in
                                     (".venv/", "__pycache__/", "data/", "artifacts/", "network-discovery", ".env")))
                self.assertIn(b"GNU AFFERO GENERAL PUBLIC LICENSE", archive.read("investBell/LICENSE"))
        finally:
            connection.close()

    def test_paper_status_is_read_only_and_uninitialized_is_not_healthy(self):
        status, result = self.request("GET", "/api/paper-status")
        self.assertEqual(status, 200)
        self.assertFalse(result["initialized"])
        self.assertFalse(result["healthy"])
        self.assertFalse(self.server.paper_ledger.exists())

    def test_model_status_does_not_create_model_or_paper_state(self):
        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory) / "models"
            with patch.object(self.server, "model_dir", model_dir), patch("investbell.data.fetch_yahoo") as fetch:
                status, result = self.request("GET", "/api/model-status")
            self.assertEqual(status, 200)
            self.assertFalse(result["initialized"])
            self.assertFalse(result["healthy"])
            self.assertEqual(result["models"], 0)
            self.assertIsNone(result["latest"])
            self.assertEqual(list(Path(directory).iterdir()), [])
            self.assertFalse(self.server.paper_ledger.exists())
            fetch.assert_not_called()

    def test_model_status_exposes_saved_parameters_without_running_research(self):
        saved = {"initialized": True, "healthy": True, "models": 1,
                 "latest": {"params": {"dca_pct": 2, "va_pct": .1, "capture_pct": 10},
                            "trained_at": "2026-09-23T00:00:00+00:00",
                            "next_retrain_at": "2028-09-23T00:00:00+00:00",
                            "model_sha256": "a" * 64}}
        with patch("investbell.server.read_model_status", return_value=saved) as read, patch("investbell.server.run") as research:
            status, result = self.request("GET", "/api/model-status")
            self.assertEqual(status, 200)
            self.assertEqual(result, saved)
            read.assert_called_once_with(self.server.model_dir)
            research.assert_not_called()
        self.assertFalse(self.server.model_dir.exists())

    def test_model_status_corruption_is_visible_without_leaking_details(self):
        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "model-private-path.json"
            original = b'{"model_sha256": "broken JSON"'
            artifact.write_bytes(original)
            with patch.object(self.server, "model_dir", Path(directory)), patch("investbell.data.fetch_yahoo") as fetch:
                status, result = self.request("GET", "/api/model-status")
            self.assertEqual(artifact.read_bytes(), original)
            self.assertEqual(list(Path(directory).iterdir()), [artifact])
            fetch.assert_not_called()
        self.assertEqual(status, 503)
        self.assertFalse(result["healthy"])
        self.assertIn("unavailable", result["message"])
        self.assertNotIn("model-private-path", json.dumps(result))

    def test_model_status_rejects_mutation(self):
        with patch("investbell.server.read_model_status") as read, patch("investbell.server.run") as research:
            status, result = self.request("POST", "/api/model-status", {"train": True})
        self.assertEqual(status, 404)
        read.assert_not_called()
        research.assert_not_called()
        self.assertFalse(self.server.model_dir.exists())

    def test_report_is_idempotent_and_not_execution(self):
        result = run(REQUEST)
        with tempfile.TemporaryDirectory() as directory:
            path, created = write_report(result, REQUEST, directory)
            self.assertTrue(created)
            original = path.read_bytes()
            second, created = write_report(result, REQUEST, directory)
            self.assertFalse(created)
            self.assertEqual(path, second)
            self.assertEqual(original, path.read_bytes())
            self.assertEqual(len(list(Path(directory).iterdir())), 1)
            self.assertIn("no broker", json.loads(original)["report_kind"])
            revised, created = write_report({**result, "model_version": "different-model"}, REQUEST, directory)
            self.assertTrue(created)
            self.assertNotEqual(path, revised)



class ShutdownTests(unittest.TestCase):
    def test_sigterm_stops_the_dashboard_cleanly(self):
        # systemd and Podman stop the container with SIGTERM; a clean exit is not an alert.
        with tempfile.TemporaryDirectory() as data_dir:
            child = subprocess.Popen([sys.executable, "-m", "investbell.server", "--port", "0", "--data-dir", data_dir],
                                     stdout=subprocess.PIPE, text=True)
            try:
                self.assertIn("InvestBell research lab", child.stdout.readline())
                child.send_signal(signal.SIGTERM)
                self.assertEqual(child.wait(timeout=10), 0)
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()
                child.stdout.close()


if __name__ == "__main__":
    unittest.main()
