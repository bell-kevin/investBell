# Running InvestBell at home

**Status, 2026-09-29:** the project is paused and the home server is powered off. These instructions are kept for a restart.

The app needs Python 3.11 or newer, its Yahoo data dependency, and a modern browser. It has no Node build step, hosted database, or cloud-service requirement. The browser assets are served locally. Yahoo requests require Internet access; the synthetic demonstration works without downloading market data.

This is a research application. Daily automation downloads Yahoo daily opening prices and writes a hypothetical strategy report. It does not connect to a brokerage, place orders, or maintain a broker paper-trading account.

**For the home server, use [the Podman deployment guide](server.md).** The Python installation and systemd instructions below are for a general Linux installation without containers.

## Identify the right computer first

The discovery results are documented locally in `docs/network-discovery.md`, which is excluded from source distribution because it contains the home network inventory. The deployment target is the former ZimaOS computer (Intel i7-6700, 16 GiB RAM), now running Ubuntu Server 26.04.1 LTS. The current workstation, `bell-server`, and the kevinbellm project computer are excluded. The server runs the container deployment described in [the home server guide](server.md), not these Python-only units.

On the intended computer, check `hostname` and `hostname -I`. Use a project directory and preserve any existing Nextcloud files and services. The examples below assume the repository is at `~/investBell` and that you have authenticated access to that machine.

## Local setup

Install Python with virtual-environment support through your operating system's normal package process. Debian/Ubuntu typically provides this as `python3-venv`; the setup script prints guidance if `ensurepip` is missing. It does not run `sudo`, install OS packages, or download a pip bootstrap script.

From the repository root:

```sh
./scripts/setup.sh
./scripts/start.sh
```

Setup creates `.venv`, installs `requirements.txt` into that environment, and creates `data/reports`. To select another installed Python interpreter, set `INVESTBELL_PYTHON=/path/to/python3` when running setup. An existing working project environment is reused.

Open <http://127.0.0.1:8765> on the same computer. The server listens on loopback by default. Stop a foreground server with Ctrl+C. Run `./scripts/start.sh --help` for server options.

## Access from another computer

An SSH tunnel keeps the server on loopback. After SSH access is configured on the confirmed host, replace the placeholders and run this on your workstation:

```sh
ssh -N -L 8765:127.0.0.1:8765 your-user@confirmed-server-address
```

Then open <http://127.0.0.1:8765> on your workstation. If that port is already used locally, use `-L 8766:127.0.0.1:8765` and browse to port 8766.

For direct access on a trusted home LAN, `./scripts/start.sh --host 0.0.0.0` listens on every network interface. This small Python server has no user authentication or TLS and is intended for localhost or a trusted LAN only. Do not expose it through router port forwarding or a public Internet proxy. Choose the loopback plus SSH approach when other network users should not access the app.

## Optional user services

The examples in `deploy/` are **systemd user units**. Nothing enables them automatically. First complete setup, then inspect the units and change `%h/investBell` if you chose a different project location. `%h` means the home directory of the user running the services. The service must run as the same user who owns the project environment and data.

Install the reviewed examples into that user's configuration:

```sh
mkdir -p ~/.config/systemd/user
cp deploy/investbell.service deploy/investbell-daily.service deploy/investbell-daily.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now investbell.service
```

The dashboard remains bound to `127.0.0.1:8765`.

Run one Yahoo report and inspect its output before opting into the schedule:

```sh
systemctl --user start investbell-daily.service
journalctl --user -u investbell-daily.service -n 50 --no-pager
```

The service uses fitted mode: SPY, a $10,000 starting portfolio, 5 basis points of slippage, and no per-order fee. It fits only DCA, DVA/VA, and capture on older Yahoo history, evaluates the selected parameters on a later block, and saves the result in `data/models/`. It reuses that model until its two-calendar-year retraining date, then refits on the first successful scheduled run after it is due. The dashboard displays saved model status. To run fixed manual percentages, pass `--model-mode fixed --dca-pct 2 --va-pct 0.1 --capture-pct 10`. The separate paper policy is never retuned by research automation. Change the daily service's command arguments to change the experiment, then run `systemctl --user daemon-reload`.

Reports identify the updated `dca-va-capture-v2` model, which permits DCA reentry in the exit session; reports written before 2026-09-29 carry an earlier name for the same rules. Earlier reports used a mandatory pause on exit days; they remain historical outputs and are not rewritten. Daily opening prices limit this replay to opening observations even though the strategy permits intraday exits.

To enable the optional timer:

```sh
systemctl --user enable --now investbell-daily.timer
systemctl --user list-timers investbell-daily.timer
```

The timer runs Monday–Friday at 18:00 America/New_York with up to five minutes of random delay. The explicit timezone follows US daylight-saving changes independently of the host timezone. The report uses completed sessions and daily opens; scheduling after the close does not introduce closing prices into the strategy. Yahoo supplies no new trading session on exchange holidays. Repeated identical runs are idempotent. A missed timer triggers once when the user manager next starts; it does not replay every missed day.

User services normally depend on a running user manager. For an unattended computer, a system administrator can enable lingering for the chosen account using `loginctl enable-linger USERNAME`. This is a separate host administration choice and is not performed by these scripts. Reboot the configured computer and verify both service status and a new report before relying on unattended operation.

## Data, status, and stopping

`data/market.sqlite3` holds cached Yahoo snapshots with provenance. `data/reports/` holds daily research reports and `data/models/` holds immutable fitted model artifacts. Keep these files on persistent storage. Stop the service and daily timer before a simple filesystem backup of `data/` so that SQLite files are copied consistently; alternatively use SQLite's online backup mechanism. Back up the project configuration and record the Python/dependency versions with the data. Yahoo outages and rate limits are reported as failures, without substituting synthetic prices.

Useful status commands:

```sh
systemctl --user status investbell.service investbell-daily.timer
journalctl --user -u investbell.service -n 50 --no-pager
journalctl --user -u investbell-daily.service -n 50 --no-pager
```

Disable automation and stop the server:

```sh
systemctl --user disable --now investbell-daily.timer investbell.service
systemctl --user stop investbell-daily.service
```

These commands preserve reports and market snapshots. These research services do not use broker credentials. The separate opt-in [Alpaca paper runner](paper-trading.md) requires its own local credentials and ledger; there is no live-trading switch.
