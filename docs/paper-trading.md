# Alpaca paper execution

This is an **experimental paper runner**, separate from the historical research dashboard. It can submit orders with virtual money to the fixed `https://paper-api.alpaca.markets` endpoint. There is no live endpoint or live-trading switch. Brokerage paper fills and future validation have not yet been demonstrated in this workspace. The [retrospective results](validation.md) do not establish a strategy advantage.

The runner has its own frozen trading rules. They differ from the research lab in two ways:

- **No-loss rule, checked at the limit price.** Like the lab, the runner holds a VA trim or full exit whose proceeds would be below the average cost of the shares sold. It tracks that cost from its actual fills. The lab checks its modeled fill price; the runner checks the sell order's limit price, which is 10 basis points below the bid. A limit sell cannot fill below its limit, so no sale the runner makes is below cost, but it holds some sales that the lab would make when the bid is within those 10 basis points of cost. Paper accounts charge no fees, and a fee would stop the runner at cash reconciliation anyway. The rule is `no_loss_sales` in the policy, on by default; like the rest of the policy, it is frozen at initialization.
- **Leveraged funds.** The lab accepts SSO, SPXL, UPRO, QLD, TQQQ and UDOW for research. The paper policy accepts only the unleveraged list (SPY, VTI, VOO, QQQ, DIA, IWM) and rejects anything else at initialization.

## Configure a dedicated account

Use a dedicated Alpaca **paper** account with no existing positions or open orders. Do not manually trade in it while the runner is active. The runner binds its ledger to that account ID. It allocates at most the policy's capital limit and treats remaining cash as an unavailable reserve; it does not use margin buying power.

1. Install dependencies with `./scripts/setup.sh`.
2. Put the paper account's keys into `.env.paper` in the project directory. This file is ignored by Git. A blank private file is prepared locally; `.env.example` is the distributable template. Never put secrets in chat or the policy JSON. Use `chmod 600 .env.paper`.
3. Review `deploy/paper-policy.json`. SIP is the default feed and requires suitable data access. If your account only has IEX, explicitly change `feed` to `iex` before initialization. IEX is one exchange's data, not a consolidated quote. An unavailable feed stops execution; there is no automatic fallback.
4. Initialize the local ledger with a read-only broker connection:

```sh
.venv/bin/python -m investbell.paper init --config deploy/paper-policy.json
```

Initialization checks account restrictions, cash, positions and open orders; it sends no orders. It freezes the next exchange session as its first session and requires 252 sessions (about one trading year) of prospective evidence. It records the policy and an execution-code digest in `data/paper.sqlite3` and a companion `.plan.json`. A later code change stops that experiment; do not reset or overwrite an active account ledger. Keep backups of the complete SQLite database using SQLite's backup API, or stop the process before copying its database and WAL files. On the home server, the nightly [backup](server.md#backups) does this.

Start the foreground **paper runner** after successful initialization; it waits until its registered first session before sending orders:

```sh
.venv/bin/python -m investbell.paper run --interval 15
```

Alternatively `step` performs one tick. A separate `deploy/investbell-paper.service` is provided for a user systemd installation under `~/investBell`; it is not installed or enabled automatically. It stops on an error and requires review instead of automatically restarting. Ctrl+C or systemd SIGTERM persists a stop and requests cancellation of owned orders. The home server uses a container instead; see the next section.

## On the home server

The home server runs everything in rootless Podman, so it runs the paper runner from `deploy/investbell-paper.container` rather than the Python service. The runner uses its own image tag, `localhost/investbell:paper`. The execution-code check covers `engine.py`, which research changes also touch, so a shared image would let a routine research update stop a running experiment. Retag only for a new experiment.

Nothing here is installed by default. Once the dedicated Alpaca paper account exists, do this as the server account that owns the other units:

1. Store the keys as a Podman secret. Write them to a private file in the format of `.env.example`, give the file to Podman, and delete it:

   ```sh
   install -m 600 /dev/null ~/paper.env
   nano ~/paper.env
   podman secret create investbell-paper-env ~/paper.env
   shred -u ~/paper.env
   ```

   Podman stores secrets unencrypted in the account's container storage, readable only by that account. The container sees them as a read-only file that only its user can read.

2. Review `deploy/paper-policy.json` on the workstation, including `feed`, then copy the source to the server and rebuild the image as described under [Updates in the server guide](server.md#updates-and-removal). Freeze that build for the runner:

   ```sh
   podman tag localhost/investbell:local localhost/investbell:paper
   ```

3. Initialize, check the result, and start the unit:

   ```sh
   cd ~/investBell
   scripts/paper-container.sh init --config deploy/paper-policy.json
   scripts/paper-container.sh status
   cp deploy/investbell-paper.container ~/.config/containers/systemd/
   systemctl --user daemon-reload
   systemctl --user start investbell-paper
   ```

`scripts/paper-container.sh` runs an `investbell.paper` command in a one-off container with the runner's image, data and keys, so the `status`, `stop`, `resume` and `acknowledge-cash` commands below work through it; `scripts/paper-container.sh evidence` runs the evidence review. The policy path is inside the image, and `/data` is `~/investbell-data`.

The unit never restarts itself. `systemctl --user stop investbell-paper`, like a shutdown, sends SIGTERM: the runner persists a stop and cancels its own orders within the 90 seconds it is allowed, and systemd records a clean stop. Any other exit is a failure and sends an [alert](server.md#alerts). After a reboot the unit starts, finds the stop saved at shutdown, exits and sends an alert. Review the account, `resume`, then start the unit again. Follow it with `journalctl --user -u investbell-paper -f`.

This was checked on the server on 2026-09-24 with a test secret and a throwaway data directory: the secret mounts readable only by the container's user, an uninitialized runner exits and sends an alert, and a stop waits for the runner and ends cleanly. It has not yet run against an Alpaca account.

## What it executes

This execution variant uses actual broker fills and raw broker opening bars. It does not copy historical simulation quantities into an account. Scheduled DCA uses the allocated cycle capital, limited by available cash and position limits. VA grows its target once per attended session and adds actual buy fill value. The following session's sell decision uses the prior session's recorded open and the reconciled strategy holdings/cash. Full exits take priority over trims. When the no-loss rule holds a sale, the runner skips that session's sell, records a warning event, counts it in the ledger's `held_sales`, and still makes the session's DCA purchase. A held full exit keeps the current cycle and VA target. Every observation records the cost basis, so unsold losses stay measurable.

Orders are fractional-share DAY limits during regular hours, starting one minute after the open and ending five minutes before the actual close, including early closes. After a sale fills completely, the next polling tick can buy again in that same session using actual net cash; it does not assume both trades received the opening print. A limit may remain unfilled. There is no market-order fallback or automatic repricing.

The broker's cash/buying-power checks remain authoritative. This first integration does not independently classify settled cash or model a different broker's cash-account rules. It never treats leveraged buying power as spendable strategy cash. Actual dividends, fees, deposits, withdrawals and corporate actions cause a reconciliation stop until reviewed; it does not invent dividend payments from an ex-date. Taxes are not deducted. These restrictions and different fill prices mean paper results must be assessed independently of historical results.

## Enforced controls

The example policy allocates $10,000; purchases use DCA 2%, VA target growth 0.1%, and capture 10%. These are test settings, not a return forecast or a personal allocation recommendation.

- Maximum order notional $20,000, daily order notional $25,000 and new-buy position value $10,000. Appreciation can exceed a cap; the runner pauses an oversized exit rather than silently splitting it or raising limits. Sell turnover is estimated conservatively at the current ask and may differ from actual price-improved proceeds.
- A 3% daily loss or 10% drawdown pauses trading and cancels working orders. These are monitoring thresholds, not guaranteed maximum losses, and do not liquidate holdings. Daily loss includes the gap from the last monitored prior-session equity. Market closure, price gaps and polling latency can cause larger losses.
- Quotes must be no more than 15 seconds old, have positive sizes, an uncrossed spread within 30 basis points, and match the selected feed. New orders must remain within 10% of the recorded session open. Limit offset is 10 basis points.
- Order intents are committed before submission. Unique account/session/leg IDs and cumulative-fill watermarks prevent ordinary restart duplicates. An uncertain timeout is resolved by looking up the same client ID; it never blindly resends.
- Partial fills are applied once and block the next leg. A working order older than 120 seconds is canceled and causes a review stop. Order quantities, limit prices and types must still match the recorded intent.
- Wrong account, changed holdings/cash, unknown orders, stale quotes, missed sessions, modified code and unexpected broker responses stop execution.

The sample thresholds are frozen at initialization. UI sliders only change research simulations. Policy changes require a separately reviewed new paper experiment, not editing the running database.

## Monitor and stop

The dashboard's paper status panel polls `/api/paper-status`. It shows a stopped runner or a heartbeat older than three minutes as unhealthy. `/api/health` measures only the research web service. The paper endpoint never submits orders or exposes credentials.

```sh
.venv/bin/python -m investbell.paper status
.venv/bin/python -m investbell.paper stop
```

`stop` writes the durable stop file before contacting the broker, cancels only this runner's orders, and checks their terminal status. A nonzero exit means cancellation remains unconfirmed; inspect the Alpaca paper dashboard. A cancellation request can race with a fill. The stop does not sell existing positions. It persists across process restarts.

Each monitored quote/equity observation is appended to the local evidence journal with plan, policy and execution-code fingerprints. `python -m investbell.paper_evidence --ledger data/paper.sqlite3` reviews the available prospective evidence without placing orders.

Critical alerts are persisted in the local ledger, emitted as structured stderr/systemd journal events, and shown in the dashboard. Remote email/SMS/push delivery is not configured. If the machine or network fails, local alerts cannot reach you and resting broker orders can remain active until canceled or expired; monitor the broker as well.

```sh
journalctl --user -u investbell-paper.service -f
```

## Review a stop

Check the Alpaca paper order history and positions against the ledger's client IDs. Unknown submission outcomes must be resolved at the broker. Never delete the ledger, remove an intent, or switch account IDs to bypass a stop.

For a temporary issue with fully reconciled orders:

```sh
.venv/bin/python -m investbell.paper resume --reason 'Reviewed broker fills and restored data access'
```

For a reviewed canceled/partially filled session, or deliberately skipped sessions, add `--acknowledge-incomplete-session`. This accepts the reconciled partial position and skips remaining orders; it never sends a replacement. It advances the VA target through acknowledged missed sessions without fictitious fills and records the review. Drawdown limits still apply after resume.

A verified cash event must be acknowledged separately with its exact signed amount and broker evidence. Example syntax only:

```sh
.venv/bin/python -m investbell.paper acknowledge-cash --kind income --expected-delta 12.34 --reason 'Verified paid dividend in paper account activity'
```

Use `income` for positive actual distributions, `expense` for negative fees, and `external` for deposits/withdrawals. External transfers adjust the excluded reserve, not strategy performance. This command requires reconciled positions and no working orders, records the adjustment, and leaves execution stopped until `resume`. Splits or unexplained position changes require investigation; there is no automatic position overwrite.

## Evidence still required

Mocks verify failure handling; they do not establish actual Alpaca fills, uptime, feed entitlement, broker accounting, or profitability. The frozen research plan requires at least 252 future exchange sessions, contemporaneous evidence and explicit benchmark comparisons. The execution variant and its risk limits must retain their own frozen configuration and broker journal. Neither a successful connection nor passing tests approves trading real money.

Primary API references: [Alpaca order handling](https://docs.alpaca.markets/us/docs/orders-at-alpaca), [fractional trading](https://docs.alpaca.markets/us/docs/fractional-trading), [paper versus live execution](https://alpaca.markets/support/difference-paper-live-trading).
