"""Optional push alerts through an ntfy topic.

INVESTBELL_NTFY_URL names the topic, for example https://ntfy.sh/<topic>, and
INVESTBELL_NTFY_TOKEN optionally authenticates to a server that requires it.
Without a URL nothing is sent. Sending never raises: a failed alert is logged
and must not stop a report or a safety stop.

The command line sends a message, or reports a failed systemd user unit with
the last lines of its journal; deploy/investbell-alert@.service runs it. Unit
reports are limited to one per unit every ten minutes.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.parse
import urllib.request


URL_VARIABLE = "INVESTBELL_NTFY_URL"
TOKEN_VARIABLE = "INVESTBELL_NTFY_TOKEN"
TIMEOUT_SECONDS = 10
# ntfy.sh turns messages over 4,096 bytes into attachments.
MAX_MESSAGE_BYTES = 3500
JOURNAL_LINES = 8
# systemd starts the alert unit on every failure, including failures it then
# restarts, so a crash loop would otherwise send an alert every few seconds.
QUIET_SECONDS = 10 * 60


def topic_url(environ=None):
    """Return the configured topic URL, or None when alerts are off."""
    environ = os.environ if environ is None else environ
    url = environ.get(URL_VARIABLE, "").strip()
    if not url:
        return None
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc or not parsed.path.strip("/"):
        raise ValueError(f"{URL_VARIABLE} must be an http or https ntfy topic URL.")
    return url


def header(text):
    # HTTP header values are Latin-1 and single-line; keep titles plain ASCII.
    return " ".join(text.encode("ascii", "replace").decode().split())


def send(title, message, *, priority="high", tags=("warning",), environ=None, opener=None):
    """Return True if ntfy accepted the alert; False if alerts are off or it failed."""
    environ = os.environ if environ is None else environ
    opener = opener or urllib.request.urlopen
    try:
        url = topic_url(environ)
        if url is None:
            return False
        body = message.encode()[:MAX_MESSAGE_BYTES].decode(errors="ignore").encode()
        request = urllib.request.Request(url, data=body, method="POST", headers={
            "Title": header(title), "Priority": priority, "Tags": ",".join(tags)})
        token = environ.get(TOKEN_VARIABLE, "").strip()
        if token:
            request.add_header("Authorization", "Bearer " + token)
        with opener(request, timeout=TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 300
    except Exception as error:
        # Never log the URL: the topic name is what keeps the alerts private.
        print(json.dumps({"time": datetime.now(timezone.utc).isoformat(), "event": "alert_failed",
                          "error": type(error).__name__, "status": getattr(error, "code", None)}),
              file=sys.stderr, flush=True)
        return False


def unit_report(unit, *, suppressed=0, run=None):
    """Return the title and message for a failed systemd user unit."""
    name = unit.removesuffix(".service")
    run = run or subprocess.run

    def journal(*match):
        try:
            return run(["journalctl", "--user", *match, "-n", str(JOURNAL_LINES), "-o", "cat", "--no-pager"],
                       capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return ""

    # Quadlet tags container output with the unit's name, which leaves out
    # Podman's and systemd's own lines. Other units fall back to the whole unit.
    lines = journal("-t", name) or journal("-u", unit)
    message = (lines + "\n\n" if lines else "") + f"Inspect: journalctl --user -u {unit} -n 50"
    if suppressed:
        message += f"\n\nIt also failed {suppressed} more time(s) after the previous alert, without separate alerts."
    return f"investBell: {name} failed", message


def quiet_path(unit, environ=None):
    """Where the unit's last alert is recorded: the per-boot runtime directory."""
    environ = os.environ if environ is None else environ
    runtime = environ.get("XDG_RUNTIME_DIR")
    return Path(runtime) / "investbell-alerts" / f"{unit}.json" if runtime else None


def read_quiet(path):
    try:
        state = json.loads(path.read_text())
        return datetime.fromisoformat(state["sent"]), int(state["suppressed"])
    except (AttributeError, OSError, ValueError, KeyError, TypeError):
        return None, 0


def write_quiet(path, sent, suppressed):
    try:
        path.parent.mkdir(mode=0o700, exist_ok=True)
        path.write_text(json.dumps({"sent": sent.isoformat(), "suppressed": suppressed}))
    except (AttributeError, OSError):
        pass


def main(argv=None, *, clock=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--unit", help="report this failed systemd user unit")
    source.add_argument("--message", help="send this text")
    parser.add_argument("--title", default="investBell alert", help="title for --message")
    parser.add_argument("--priority", choices=("min", "low", "default", "high", "urgent"), default="high")
    args = parser.parse_args(argv)
    try:
        if topic_url() is None:
            parser.exit(1, f"{URL_VARIABLE} is not set; no alert sent.\n")
    except ValueError as error:
        parser.exit(2, f"{error}\n")
    if not args.unit:
        return 0 if send(args.title, args.message, priority=args.priority) else 1
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    path = quiet_path(args.unit)
    sent, suppressed = read_quiet(path)
    if sent and 0 <= (now - sent).total_seconds() < QUIET_SECONDS:
        write_quiet(path, sent, suppressed + 1)
        print(json.dumps({"time": now.isoformat(), "event": "alert_suppressed", "unit": args.unit,
                          "previous_alert": sent.isoformat()}), file=sys.stderr, flush=True)
        return 0
    if not send(*unit_report(args.unit, suppressed=suppressed), priority=args.priority):
        return 1
    write_quiet(path, now, 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
