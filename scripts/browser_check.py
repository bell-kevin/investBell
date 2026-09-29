"""Exercise the running dashboard. Optional dependency: Playwright + Chromium."""

import argparse
import csv
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    parser.add_argument("--artifacts", default="artifacts")
    args = parser.parse_args()
    output = Path(args.artifacts)
    output.mkdir(parents=True, exist_ok=True)
    errors = []
    capture_preview = None
    model_status = {"initialized": False, "healthy": False, "models": 0, "latest": None}
    model_http_status = 200
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        # Playwright reports a light device by default; keep the baseline screenshots dark.
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, device_scale_factor=1, color_scheme="dark")
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))

        # Keep browser verification reproducible without a Yahoo connection.
        # Demo requests still exercise the real local API and strategy engine.
        def route_run(route):
            if route.request.post_data_json.get("source") == "yahoo":
                route.fulfill(status=502, content_type="application/json",
                              body=json.dumps({"error": "Yahoo unavailable (browser test)"}))
            elif capture_preview:
                response = route.fetch()
                payload = response.json()
                payload["selected"]["next_action"] = capture_preview
                route.fulfill(response=response, json=payload)
            else:
                route.continue_()

        page.route("**/api/run", route_run)
        page.route("**/api/model-status", lambda route: route.fulfill(
            status=model_http_status, content_type="application/json", body=json.dumps(model_status)))
        page.goto(args.url)
        page.wait_for_function("!document.querySelector('#run-button').disabled", timeout=90000)
        yahoo_status = page.locator("#status").inner_text()
        yahoo_loaded = page.locator("#error").is_hidden()
        assert not yahoo_loaded
        assert "Yahoo unavailable" in page.locator("#error").inner_text()
        assert "AWAITING DATA" in page.locator("#source-badge").inner_text()
        assert page.locator("#capture_pct").count() == 1
        page.wait_for_function("document.querySelector('#model-status-heading').textContent === 'No fitted model yet'")
        assert page.locator("#model-provenance").is_hidden()
        assert "DVA (VA)" in page.locator(".subtitle").inner_text()

        page.select_option("#source", "demo")
        page.fill("#start", "2024-01-01")
        page.fill("#end", "2025-01-01")
        page.click("#run-button")
        page.wait_for_function("!document.querySelector('#run-button').disabled")
        assert "SYNTHETIC" in page.locator("#source-badge").inner_text()
        assert page.locator("#ledger-body tr").count() == 125
        assert "DVA / day" in page.locator("#ledger-head").inner_text()
        assert page.locator("#equity-chart polyline").count() == 2
        assert page.locator("#next-action strong").inner_text()
        page.select_option("#slice", "10")
        page.select_option("#color-metric", "max_drawdown_pct")
        canvas = page.locator("#cube-canvas")
        canvas.focus()
        page.keyboard.press("ArrowRight")
        page.click("#reset-view")

        page.locator("#ledger-body button").first.click()
        page.wait_for_function("!document.querySelector('#run-button').disabled")
        assert "DCA 0.5%" in page.locator("#selected-params").inner_text()
        assert page.locator("#ledger-body tr").count() == 125
        with page.expect_download() as download:
            page.click("#export-json")
        exported = json.loads(Path(download.value.path()).read_text())
        assert exported["result"]["source"]["kind"] == "demo"
        assert exported["result"]["selected"]["params"]["dca_pct"] == 0.5
        assert exported["result"]["selected"]["params"]["capture_pct"] == 2
        assert len(exported["result"]["grid"]) == 125
        assert "capture_count" in exported["result"]["selected"]["metrics"]
        with page.expect_download() as download:
            page.click("#export-csv")
        grid_rows = list(csv.DictReader(io.StringIO(Path(download.value.path()).read_text())))
        assert len(grid_rows) == 125
        assert "capture_pct" in grid_rows[0]

        page.select_option("#table-mode", "trades")
        assert page.locator("#ledger-body tr").count() > 0
        with page.expect_download() as download:
            page.click("#export-csv")
        assert '"signal_date"' in Path(download.value.path()).read_text()
        with page.expect_download() as download:
            page.get_by_role("link", name="AGPLv3 · Download source ↓").click()
        assert download.value.suggested_filename == "investBell-source.zip"
        page.screenshot(path=str(output / "desktop-demo.png"), full_page=True)

        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "mobile-demo.png"), full_page=True)

        # Auto follows the device setting. Light and Dark override it, repaint the
        # cube and chart without a rerun, persist, and reach other open tabs.
        theme = "document.documentElement.dataset.theme"
        assert page.get_attribute("[data-theme-choice=auto]", "aria-pressed") == "true"
        # Browsers report a device theme change at their next rendering update,
        # not during emulate_media, so wait for the page to follow it.
        page.emulate_media(color_scheme="light")
        page.wait_for_function(f"{theme} === 'light'", timeout=5000)
        page.emulate_media(color_scheme="dark")
        page.wait_for_function(f"{theme} === 'dark'", timeout=5000)
        dark_cube = page.evaluate("document.querySelector('#cube-canvas').toDataURL()")
        page.click("[data-theme-choice=light]")
        assert page.evaluate(theme) == "light"
        assert page.get_attribute("[data-theme-choice=light]", "aria-pressed") == "true"
        assert page.get_attribute("[data-theme-choice=auto]", "aria-pressed") == "false"
        assert page.evaluate("getComputedStyle(document.documentElement).backgroundColor") == "rgb(243, 246, 245)"
        assert page.evaluate("getComputedStyle(document.querySelector('#equity-chart .chart-equity')).stroke") == "rgb(11, 118, 80)"
        assert page.evaluate("document.querySelector('#cube-canvas').toDataURL()") != dark_cube
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(output / "mobile-light.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.screenshot(path=str(output / "desktop-light.png"), full_page=True)
        other = context.new_page()
        other.goto(args.url.rstrip("/") + "/methodology.html")
        assert other.evaluate(theme) == "light"
        other.click("[data-theme-choice=dark]")
        page.wait_for_function(f"{theme} === 'dark'")
        assert page.get_attribute("[data-theme-choice=dark]", "aria-pressed") == "true"
        other.click("[data-theme-choice=auto]")
        page.wait_for_function("document.querySelector('[data-theme-choice=auto]').getAttribute('aria-pressed') === 'true'")
        other.close()

        # A $1,000 cycle buys $500 at each of two opens ($100, then $200),
        # producing 7.5 shares and a $1,500 full-exit value. A 50% DCA rule
        # can therefore spend $750 of the exit proceeds in that same session.
        # Replace only the paper plan to exercise its full-exit rendering branch.
        capture_preview = {"action": "capture", "capture": True,
                           "sell_shares": 7.5, "estimated_buy_dollars": 750,
                           "buy_dollars_contingent_on_exit": True,
                           "available_cash": 0, "cycle_start_equity": 1000,
                           "reference_open": 200, "as_of": "2024-01-02"}
        page.click("#run-button")
        page.wait_for_function("!document.querySelector('#run-button').disabled")
        assert page.locator("#next-action strong").inner_text() == "Full exit, then DCA reentry"
        preview = page.locator("#next-action").inner_text()
        assert "Estimated same-session DCA purchase: $750.00" in preview
        assert "actual cash after the exit" in preview
        assert "NaN" not in preview
        page.set_viewport_size({"width": 1440, "height": 1000})
        page.screenshot(path=str(output / "desktop-capture-plan.png"), full_page=True)
        capture_preview = {**capture_preview, "buy_dollars_contingent_on_exit": False,
                           "estimated_buy_dollars": 20, "available_cash": 900}
        page.click("#run-button")
        page.wait_for_function("!document.querySelector('#run-button').disabled")
        preview = page.locator("#next-action").inner_text()
        assert "estimated proceeds cannot cover the sale fee" in preview
        assert "Estimated ordinary DCA purchase: $20.00" in preview
        assert "existing cycle budget if the exit cannot execute" in preview
        capture_preview = None

        # Provider errors must remain visible and must never replace history with demo data.
        page.select_option("#source", "yahoo")
        page.click("#run-button")
        page.wait_for_function("!document.querySelector('#run-button').disabled")
        assert "Yahoo unavailable" in page.locator("#error").inner_text()
        assert "Earlier results remain" in page.locator("#status").inner_text()
        assert "SYNTHETIC" in page.locator("#source-badge").inner_text()

        # Saved research fits have visible provenance and do not change the
        # independent manual controls. Model-status errors must hide provenance.
        now = datetime.now(timezone.utc)
        model_status = {"initialized": True, "healthy": True, "models": 1,
                        "latest": {"params": {"dca_pct": 3, "va_pct": .2, "capture_pct": 20},
                                   "trained_at": now.isoformat(),
                                   "next_retrain_at": (now + timedelta(days=730)).isoformat(),
                                   "model_sha256": "abc123" * 10 + "abcd",
                                   "data": {"source": {"symbol": "SPY"}, "observed_end": "2026-09-22"}}}
        page.reload()
        page.wait_for_function("document.querySelector('#model-status-heading').textContent === 'Saved research fit'")
        assert "DCA 3% / day · DVA 0.2% / day · Capture 20%" in page.locator("#model-status-message").inner_text()
        assert "Next refit" in page.locator("#model-status-message").inner_text()
        assert "Yahoo SPY · Observations through 2026-09-22" in page.locator("#model-provenance").inner_text()
        assert page.input_value("#dca_pct") == "2"
        assert page.input_value("#va_pct") == "0.1"
        assert page.input_value("#capture_pct") == "10"
        page.screenshot(path=str(output / "desktop-fitted-model.png"), full_page=True)
        model_status["latest"]["next_retrain_at"] = (now - timedelta(days=1)).isoformat()
        page.reload()
        page.wait_for_function("document.querySelector('#model-status-heading').textContent === 'Research fit is due for retraining'")
        assert "next successful daily research run" in page.locator("#model-status-message").inner_text()
        model_http_status = 503
        model_status = {"healthy": False, "message": "Fitted-model status is unavailable."}
        page.reload()
        page.wait_for_function("document.querySelector('#model-status-heading').textContent === 'Fitted-model status is unavailable'")
        assert page.locator("#model-provenance").is_hidden()
        assert "unavailable" in page.locator("#model-status-message").inner_text()
        assert not errors, errors
        browser.close()
    print(json.dumps({"browser_checks": "passed", "javascript_errors": errors,
                      "yahoo_loaded": yahoo_loaded, "yahoo_status": yahoo_status,
                      "artifacts": str(output)}, indent=2))


if __name__ == "__main__":
    main()
