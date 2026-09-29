"""Small self-hosted, paper-research HTTP application using the standard library."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import signal
import threading
from urllib.parse import urlsplit
import zipfile

from . import __version__
from .data import DataUnavailable, PriceStore
from .service import config, run
from .paper import read_status
from .training import read_status as read_model_status


ROOT = Path(__file__).resolve().parent.parent


def source_archive():
    """Offer the running project's source without market data or local secrets."""
    files = [ROOT / name for name in ("LICENSE", "README.md", "requirements.txt", ".gitignore", "Containerfile", ".containerignore")]
    suffixes = {".py", ".js", ".css", ".html", ".md", ".sh", ".service", ".timer", ".container", ".yml", ".yaml"}
    for directory in ("investbell", "static", "scripts", "tests", "deploy"):
        files.extend(path for path in (ROOT / directory).rglob("*")
                     if path.suffix in suffixes and "__pycache__" not in path.parts)
    # Research belongs in the source; private LAN inventories stay on this host.
    files.extend(ROOT / "docs" / name for name in ("strategy-requirements.md", "slide-audit.md", "source-research.md", "deployment.md", "server.md", "validation.md", "paper-trading.md"))
    files.append(ROOT / "deploy" / "paper-policy.json")
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(files)):
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(ROOT):
                archive.write(path, "investBell/" + str(path.relative_to(ROOT)))
    return stream.getvalue()


class Handler(SimpleHTTPRequestHandler):
    server_version = "InvestBell/" + __version__

    def __init__(self, *args, directory=None, **kwargs):
        super().__init__(*args, directory=str(directory or ROOT / "static"), **kwargs)

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "DENY")
        super().end_headers()

    def json_response(self, status, payload):
        body = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/health":
            return self.json_response(200, {"ok": True, "version": __version__, "paper_only": True, "component": "research_dashboard", "execution_health": "/api/paper-status"})
        if path == "/api/paper-status":
            try:
                result = read_status(self.server.paper_ledger)
                return self.json_response(200 if not result.get("initialized") or result["healthy"] else 503, result)
            except Exception:
                return self.json_response(503, {"healthy": False, "message": "Paper execution status is unavailable; inspect the local journal."})
        if path == "/api/model-status":
            try:
                result = read_model_status(self.server.model_dir)
                return self.json_response(200 if not result.get("initialized") or result["healthy"] else 503, result)
            except Exception:
                return self.json_response(503, {"healthy": False, "message": "Fitted-model status is unavailable. Check the latest daily research run."})
        if path == "/api/config":
            return self.json_response(200, config())
        if path == "/api/source":
            body = source_archive()
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Disposition", 'attachment; filename="investBell-source.zip"')
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if path.startswith("/api/"):
            return self.json_response(404, {"error": "Unknown API endpoint."})
        super().do_GET()

    def list_directory(self, path):
        self.send_error(404, "Not found")
        return None

    def do_POST(self):
        if urlsplit(self.path).path != "/api/run":
            return self.json_response(404, {"error": "Unknown API endpoint."})
        origin = self.headers.get("Origin")
        if origin and urlsplit(origin).netloc != self.headers.get("Host"):
            return self.json_response(403, {"error": "Cross-origin requests are not accepted."})
        if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json":
            return self.json_response(415, {"error": "Send Content-Type: application/json."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 32768:
                return self.json_response(413, {"error": "Send a JSON body of at most 32 KiB."})
            self.connection.settimeout(30)
            request = json.loads(self.rfile.read(length))
        except (ValueError, UnicodeError):
            return self.json_response(400, {"error": "Malformed JSON request."})
        except TimeoutError:
            return self.json_response(408, {"error": "Request body timed out."})
        if not self.server.run_lock.acquire(blocking=False):
            return self.json_response(429, {"error": "A research run is already in progress. Try again shortly."})
        try:
            result = run(request, store=self.server.price_store)
            self.json_response(200, result)
        except ValueError as error:
            self.json_response(400, {"error": str(error)})
        except DataUnavailable as error:
            self.json_response(502, {"error": str(error)})
        except Exception as error:
            self.log_error("Research run failed: %s", type(error).__name__)
            self.json_response(500, {"error": "The research run failed. Check the server log."})
        finally:
            self.server.run_lock.release()


def make_server(host="127.0.0.1", port=8765, *, data_dir="data", static_dir=None):
    server = ThreadingHTTPServer((host, port), partial(Handler, directory=static_dir))
    server.daemon_threads = True
    server.run_lock = threading.Lock()
    server.paper_ledger = Path(data_dir) / "paper.sqlite3"
    server.model_dir = Path(data_dir) / "models"
    server.price_store = PriceStore(Path(data_dir) / "market.sqlite3")
    return server


def stop_on_sigterm(_signum, _frame):
    raise KeyboardInterrupt


def main(argv=None):
    parser = argparse.ArgumentParser(description="Serve the InvestBell open-price research lab (no broker orders).")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data-dir", default="data")
    args = parser.parse_args(argv)
    server = make_server(args.host, args.port, data_dir=args.data_dir)
    print(f"InvestBell research lab: http://{args.host}:{server.server_port} (paper research only)", flush=True)
    # A container's main process ignores SIGTERM without a handler, so Podman
    # would kill it after ten seconds and systemd would report a failure.
    signal.signal(signal.SIGTERM, stop_on_sigterm)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
