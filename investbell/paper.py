"""Persistent, fail-closed Alpaca PAPER execution. No live endpoint or switch."""
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_DOWN, ROUND_UP
import argparse
import fcntl
import hashlib
import json
import math
import os
import signal
from pathlib import Path
import sqlite3
import sys
import time
from zoneinfo import ZoneInfo

from .engine import SYMBOLS, number

UTC = timezone.utc
NY = ZoneInfo('America/New_York')
TERMINAL = {'filled', 'canceled', 'expired', 'rejected'}


class SafetyStop(RuntimeError):
    pass


def stamp():
    return datetime.now(UTC).isoformat()


def instant(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise SafetyStop('Broker timestamp has no timezone.')
    return result.astimezone(UTC)


def finite(value):
    result = float(value)
    if not math.isfinite(result):
        raise SafetyStop('Nonfinite broker number.')
    return result


@dataclass(frozen=True)
class Policy:
    symbol: str = 'SPY'
    capital_limit: float = 10000
    dca_pct: float = 2
    va_pct: float = .1
    capture_pct: float = 10
    max_order_notional: float = 20000
    max_daily_notional: float = 25000
    max_position_value: float = 10000
    max_daily_loss_pct: float = 3
    max_drawdown_pct: float = 10
    max_spread_bps: float = 30
    limit_offset_bps: float = 10
    max_quote_age_seconds: float = 15
    max_order_age_seconds: float = 120
    max_reference_gap_pct: float = 10
    feed: str = 'sip'
    no_loss_sales: bool = True

    def __post_init__(self):
        if self.symbol not in SYMBOLS or self.feed not in {'sip', 'iex'}:
            raise ValueError('Choose an approved ETF and explicit sip or iex feed.')
        if type(self.no_loss_sales) is not bool:
            raise ValueError('no_loss_sales must be true or false.')
        bounds = {'capital_limit': (100, 1000000), 'dca_pct': (.01, 100), 'va_pct': (0, 5),
                  'capture_pct': (.1, 1000), 'max_daily_loss_pct': (.01, 100),
                  'max_drawdown_pct': (.01, 100), 'max_spread_bps': (.1, 100),
                  'limit_offset_bps': (0, 100), 'max_quote_age_seconds': (1, 60),
                  'max_order_age_seconds': (5, 3600), 'max_reference_gap_pct': (.1, 25)}
        for name in ('max_order_notional', 'max_daily_notional', 'max_position_value'):
            bounds[name] = (1, 1000000)
        for name, limits in bounds.items():
            object.__setattr__(self, name, number(getattr(self, name), name, *limits))


class Ledger:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stop_path = Path(str(self.path) + '.stop')
        self.db = sqlite3.connect(self.path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA journal_mode=WAL;
            PRAGMA synchronous=FULL;
            CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS orders (client_id TEXT PRIMARY KEY, session TEXT NOT NULL,
                leg TEXT NOT NULL, payload TEXT NOT NULL, broker_id TEXT, status TEXT NOT NULL,
                filled_qty REAL NOT NULL DEFAULT 0, filled_value REAL NOT NULL DEFAULT 0,
                created TEXT NOT NULL, UNIQUE(session, leg));
            CREATE TABLE IF NOT EXISTS observations (id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL,
                level TEXT NOT NULL, message TEXT NOT NULL);
        ''')
        self.db.commit()
        os.chmod(self.path, 0o600)

    @contextmanager
    def lock(self):
        with open(str(self.path) + '.lock', 'a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle, fcntl.LOCK_UN)

    def state(self):
        row = self.db.execute('SELECT payload FROM state WHERE id=1').fetchone()
        if not row:
            raise SafetyStop('Initialize a dedicated, flat paper account first.')
        return json.loads(row[0])

    def save(self, state):
        self.db.execute('INSERT OR REPLACE INTO state VALUES (1, ?)',
                        (json.dumps(state, allow_nan=False, sort_keys=True),))
        self.db.commit()

    def event(self, level, message):
        self.db.execute('INSERT INTO events(timestamp,level,message) VALUES (?,?,?)', (stamp(), level, message))
        self.db.commit()
        # Structured stderr is collected by systemd/journald. No credentials or raw HTTP body.
        print(json.dumps({'time': stamp(), 'level': level, 'message': message}), file=sys.stderr, flush=True)

    def halt(self, reason):
        self.db.rollback()
        state = self.state()
        state['halted'] = reason
        self.save(state)
        self.event('critical', reason)
        raise SafetyStop(reason)


def canonical_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def execution_digest():
    root = Path(__file__).parent
    digest = hashlib.sha256()
    for name in ('paper.py', 'broker.py', 'calendar.py', 'engine.py'):
        digest.update(name.encode())
        digest.update((root/name).read_bytes())
    return digest.hexdigest()


def initialize(ledger, broker, policy, *, now=None):
    now = now or datetime.now(UTC)
    with ledger.lock():
        if ledger.db.execute('SELECT 1 FROM state').fetchone():
            raise SafetyStop('Ledger already initialized; it cannot silently reset an account.')
        account = broker.account()
        if broker.positions() or broker.list_orders(status='open'):
            raise SafetyStop('Use a dedicated paper account with no positions or open orders.')
        check_account(account)
        cash = finite(account['cash'])
        if cash < policy.capital_limit:
            raise SafetyStop('Paper cash is below the configured allocation.')
        from .calendar import expected_sessions
        next_day = now.astimezone(NY).date()+timedelta(days=1)
        planned_sessions = expected_sessions(next_day, next_day+timedelta(days=400))
        manifest = {'created_at': now.isoformat(), 'first_session': planned_sessions[0],
                    'min_sessions': 252, 'min_end_session': planned_sessions[251],
                    'policy': asdict(policy), 'execution_sha256': execution_digest(),
                    'mode': 'alpaca-paper-only', 'validation_status': 'unproven; prospective paper evidence required'}
        manifest['plan_sha256'] = canonical_digest(manifest)
        state = {'account_id': account['id'], 'policy': asdict(policy), 'created_at': now.isoformat(), 'execution_sha256': execution_digest(),
                 'evidence_plan': manifest,
                 'expected_cash': cash, 'expected_qty': 0, 'reserve_cash': cash-policy.capital_limit,
                 'cost_basis': 0, 'held_sales': 0,
                 'cycle_start': policy.capital_limit, 'target': 0, 'peak': policy.capital_limit,
                 'day_start_equity': policy.capital_limit, 'session': None, 'reference_open': None,
                 'capture': False, 'sell_qty': 0, 'sell_done': False, 'buy_done': False,
                 'halted': None, 'last_success': None, 'mode': 'alpaca-paper-only'}
        ledger.save(state)
        Path(str(ledger.path)+'.plan.json').write_text(json.dumps(manifest, indent=2)+'\n')
        ledger.event('info', 'Initialized dedicated paper allocation; live execution is unavailable.')
        return state


def check_account(account):
    if account.get('status') != 'ACTIVE' or account.get('currency', 'USD') != 'USD':
        raise SafetyStop('Broker account is not ACTIVE USD.')
    if account.get('trading_blocked') or account.get('account_blocked') or account.get('trade_suspended_by_user'):
        raise SafetyStop('Broker account blocks trading.')
    if finite(account['cash']) < -.01:
        raise SafetyStop('Borrowed cash is not allowed.')


def reconcile(ledger, broker, *, reviewed=False, allow_cash_difference=False):
    """Apply cumulative fills exactly once, then compare against actual broker cash/shares."""
    state = ledger.state()
    for row in ledger.db.execute('SELECT * FROM orders ORDER BY created').fetchall():
        if row['status'] in TERMINAL:
            continue
        order = broker.get_order_by_client_id(row['client_id'])
        if order is None:
            ledger.halt('Unresolved submitted order; no automatic resend. Inspect its client ID at the broker.')
        payload = json.loads(row['payload'])
        if order.get('client_order_id') != row['client_id'] or order.get('symbol') != payload['symbol'] or order.get('side') != payload['side']:
            ledger.halt('Broker order identity mismatch.')
        if (abs(finite(order.get('qty', -1))-payload['qty']) > 1e-8 or
                order.get('type') != 'limit' or order.get('time_in_force') != 'day' or
                abs(finite(order.get('limit_price', -1))-payload['limit_price']) > .00001 or order.get('replaced_by')):
            ledger.halt('Broker order terms differ from the durable intent; possible external replacement.')
        qty = finite(order.get('filled_qty', 0))
        average = finite(order.get('filled_avg_price') or 0)
        if qty > 0 and average <= 0:
            ledger.halt('Filled quantity has no valid positive fill price.')
        value = qty * average
        if qty < row['filled_qty'] - 1e-8 or qty > payload['qty'] + 1e-8 or value < row['filled_value'] - .01:
            ledger.halt('Broker cumulative fill moved backwards or exceeded order quantity.')
        delta_qty, delta_value = qty-row['filled_qty'], value-row['filled_value']
        sign = 1 if payload['side'] == 'buy' else -1
        held = state['expected_qty']
        state['expected_qty'] += sign * delta_qty
        state['expected_cash'] -= sign * delta_value
        if sign == 1:
            state['target'] += delta_value
            state['cost_basis'] += delta_value
        elif delta_qty > 0:
            # Average cost: a sale removes its proportional share of the basis.
            state['cost_basis'] *= max(0, state['expected_qty'])/held if held > 1e-12 else 0
        status = order['status']
        if status == 'filled':
            if abs(qty-payload['qty']) > 1e-8:
                ledger.halt('Filled order has an incomplete quantity.')
            state[row['leg'] + '_done'] = True
            if row['leg'] == 'sell' and state['capture']:
                if abs(state['expected_qty']) > 1e-7:
                    ledger.halt('Capture did not liquidate the full position.')
                state['cycle_start'] = state['expected_cash'] - state['reserve_cash']
                state['target'] = state['cost_basis'] = 0
        # Fill watermark and account state commit in one transaction.
        try:
            ledger.db.execute('UPDATE orders SET broker_id=?,status=?,filled_qty=?,filled_value=? WHERE client_id=?',
                              (order['id'], status, qty, value, row['client_id']))
            ledger.save(state)
        except BaseException:
            ledger.db.rollback()
            raise
        if status in TERMINAL and status != 'filled' and not reviewed:
            ledger.halt('Order ended ' + status + '; reconciled any partial fill. Manual review required.')
    account, positions = broker.account(), broker.positions()
    check_account(account)
    if account['id'] != state['account_id']:
        ledger.halt('Account identity changed. Refusing to use this ledger with another account.')
    policy = Policy(**state['policy'])
    if any(p['symbol'] != policy.symbol for p in positions):
        ledger.halt('Unexpected position in dedicated account.')
    qty = sum(finite(p['qty']) for p in positions)
    if qty < -1e-8 or abs(qty-state['expected_qty']) > 1e-7:
        ledger.halt('Broker shares do not reconcile; possible external trade or corporate action.')
    if not allow_cash_difference and abs(finite(account['cash'])-state['expected_cash']) > .02:
        ledger.halt('Broker cash does not reconcile; inspect fees, dividends, deposits, or external trades.')
    known = {r[0] for r in ledger.db.execute('SELECT client_id FROM orders')}
    if any(o.get('client_order_id') not in known for o in broker.list_orders(status='open')):
        ledger.halt('Unrecognized open order in dedicated account.')
    return account, positions


def cancel_owned(ledger, broker):
    failures = []
    for row in ledger.db.execute('SELECT * FROM orders').fetchall():
        if row['status'] in TERMINAL:
            continue
        try:
            order = broker.get_order_by_client_id(row['client_id'])
            if order and order['status'] not in TERMINAL:
                broker.cancel_order(order['id'])
                confirmed = broker.get_order_by_client_id(row['client_id'])
                if not confirmed or confirmed['status'] not in TERMINAL:
                    failures.append(row['client_id'])
            elif not order:
                failures.append(row['client_id'])
        except Exception:
            failures.append(row['client_id'])
    if failures:
        ledger.event('critical', 'Cancellation unresolved; inspect broker orders: ' + ', '.join(failures))
    return not failures


def stop(ledger, broker=None):
    ledger.stop_path.write_text('Emergency stop requested ' + stamp() + '\n')
    with ledger.lock():
        state = ledger.state()
        state['halted'] = 'Emergency stop requested; no automatic liquidation.'
        ledger.save(state)
        ledger.event('critical', state['halted'])
        return cancel_owned(ledger, broker) if broker else False


def quote_check(quote, now, policy):
    age = (now-instant(quote['timestamp'])).total_seconds()
    bid, ask = finite(quote['bid_price']), finite(quote['ask_price'])
    if not -2 <= age <= policy.max_quote_age_seconds:
        raise SafetyStop('Quote is stale or future-dated.')
    if quote.get('feed') != policy.feed or quote.get('symbol') != policy.symbol:
        raise SafetyStop('Quote feed or symbol mismatch.')
    if not 0 < bid <= ask or finite(quote['bid_size']) <= 0 or finite(quote['ask_size']) <= 0:
        raise SafetyStop('Quote is empty or crossed.')
    if (ask-bid)/((ask+bid)/2)*10000 > policy.max_spread_bps:
        raise SafetyStop('Quote spread exceeds policy.')
    return bid, ask


def submit_leg(ledger, broker, leg, quote, now, close_deadline):
    state = ledger.state()
    policy = Policy(**state['policy'])
    if ledger.stop_path.exists() or state['halted']:
        raise SafetyStop('Paper execution is stopped.')
    bid, ask = quote_check(quote, now, policy)
    if abs((bid+ask)/2 / state['reference_open'] - 1)*100 > policy.max_reference_gap_pct:
        raise SafetyStop('Quote moved beyond allowed distance from session open.')
    side = 'sell' if leg == 'sell' else 'buy'
    offset = Decimal(str(policy.limit_offset_bps))/10000
    px = Decimal(str(bid))*(1-offset) if side == 'sell' else Decimal(str(ask))*(1+offset)
    price = float(px.quantize(Decimal('.01'), rounding=ROUND_UP if side == 'sell' else ROUND_DOWN))
    if side == 'sell':
        qty = min(state['sell_qty'], state['expected_qty'])
    else:
        available = max(0, state['expected_cash']-state['reserve_cash'])
        position_room = max(0, policy.max_position_value-state['expected_qty']*ask)
        budget = min(state['cycle_start']*policy.dca_pct/100, available, position_room)
        qty = Decimal(str(budget))/Decimal(str(price))
    qty = float(Decimal(str(qty)).quantize(Decimal('.000000001'), rounding=ROUND_DOWN))
    if side == 'sell' and qty > 0 and policy.no_loss_sales:
        # A limit sell cannot fill below its limit, so a sale whose limit
        # proceeds cover the average cost of the shares sold cannot sell at a loss.
        cost = state['cost_basis']*qty/state['expected_qty']
        if qty*price + 1e-9 < cost:
            state['sell_done'] = True
            state['held_sales'] += 1
            ledger.save(state)
            ledger.event('warning', f"No-loss rule held a {'capture exit' if state['capture'] else 'VA trim'} of "
                                    f"{qty:.9f} shares: limit proceeds {qty*price:.2f} are below their cost {cost:.2f}.")
            return 'held_sale'
    if qty <= 0 or qty*price < 1:
        if side == 'sell' and qty > 0:
            raise SafetyStop('Sale is below supported minimum; review residual shares.')
        state[leg+'_done'] = True
        ledger.save(state)
        return 'no_funded_order'
    notional = qty*price
    # Sell value uses ask as conservative turnover estimate (a limit sell may improve).
    risk_notional = qty * max(price, ask)
    daily = sum(json.loads(r[0])['risk_notional'] for r in ledger.db.execute('SELECT payload FROM orders WHERE session=?', (state['session'],)))
    if risk_notional > policy.max_order_notional + .001 or daily+risk_notional > policy.max_daily_notional + .001:
        raise SafetyStop('Order or daily notional limit exceeded.')
    if side == 'buy' and notional > state['expected_cash']-state['reserve_cash']+.001:
        raise SafetyStop('Order would consume reserve or borrow cash.')
    client_id = 'ibp-' + hashlib.sha256((state['account_id']+state['created_at']+state['session']+leg).encode()).hexdigest()[:40]
    payload = {'client_order_id': client_id, 'symbol': policy.symbol, 'side': side,
               'qty': qty, 'limit_price': price, 'risk_notional': risk_notional}
    ledger.db.execute('INSERT INTO orders(client_id,session,leg,payload,status,created) VALUES (?,?,?,?,?,?)',
                      (client_id, state['session'], leg, json.dumps(payload), 'submitting', now.isoformat()))
    ledger.db.commit()  # Durable intent BEFORE the external mutation.
    if ledger.stop_path.exists():
        ledger.db.execute("UPDATE orders SET status='canceled' WHERE client_id=?", (client_id,))
        ledger.db.commit()
        raise SafetyStop('Emergency stop arrived before submission.')
    def before_post():
        if ledger.stop_path.exists():
            raise SafetyStop('Emergency stop arrived during broker preflight.')
    try:
        broker.submit_order(**{k:v for k,v in payload.items() if k != 'risk_notional'},
                            valid_until=min(instant(quote['timestamp'])+timedelta(seconds=policy.max_quote_age_seconds), close_deadline),
                            pre_submit=before_post)
    except Exception as error:
        if isinstance(error, SafetyStop) or getattr(error, 'ambiguous', None) is False:
            ledger.db.execute("UPDATE orders SET status='rejected' WHERE client_id=?", (client_id,))
            ledger.db.commit()
        ledger.event('critical', 'Order submission outcome unresolved (' + type(error).__name__ + '); no automatic resend.')
        raise SafetyStop('Submission failed or is ambiguous; reconcile by client ID before continuing.') from None
    if ledger.stop_path.exists():
        cancel_owned(ledger, broker)
        raise SafetyStop('Emergency stop arrived during submission; cancellation requested.')
    ledger.event('info', 'Submitted paper ' + leg + ' with client ID ' + client_id)
    return 'submitted_' + leg


def step(ledger, broker, *, now=None):
    fixed_now = now
    now = now or datetime.now(UTC)
    with ledger.lock():
        try:
            state = ledger.state()
            plan = state['evidence_plan']
            if (canonical_digest({k:v for k,v in plan.items() if k != 'plan_sha256'}) != plan['plan_sha256'] or
                    state['policy'] != plan['policy']):
                raise SafetyStop('Frozen paper plan or policy changed; execution is stopped.')
            if state.get('execution_sha256') != execution_digest():
                raise SafetyStop('Execution code changed after paper initialization; freeze and review a new experiment.')
            if ledger.stop_path.exists() or state['halted']:
                cancel_owned(ledger, broker)
                raise SafetyStop(state['halted'] or 'Emergency stop file exists.')
            account, positions = reconcile(ledger, broker)
            state = ledger.state()
            policy = Policy(**state['policy'])
            clock = broker.clock()
            now = fixed_now or datetime.now(UTC)
            if abs((now-instant(clock['timestamp'])).total_seconds()) > 30:
                raise SafetyStop('Broker clock is stale or local clock is incorrect.')
            if not clock['is_open']:
                state['last_success'] = now.isoformat()
                ledger.save(state)
                return 'market_closed'
            session = now.astimezone(NY).date().isoformat()
            if session < state['evidence_plan']['first_session']:
                state['last_success'] = now.isoformat()
                ledger.save(state)
                return 'awaiting_registered_start'
            if state['session'] is None and session != state['evidence_plan']['first_session']:
                raise SafetyStop('Registered first paper session was missed; register a new experiment.')
            from .calendar import expected_sessions
            if not expected_sessions(session, (now.astimezone(NY).date()+timedelta(days=1)).isoformat()):
                raise SafetyStop('Broker clock disagrees with exchange calendar.')
            sessions = broker.calendar(session, session)
            if len(sessions) != 1 or sessions[0]['date'] != session:
                raise SafetyStop('Broker session calendar is unavailable.')
            # Wait a minute for the opening bar; stop new orders near actual/early close.
            opened = datetime.fromisoformat(session+'T'+sessions[0]['open']).replace(tzinfo=NY)
            closed = datetime.fromisoformat(session+'T'+sessions[0]['close']).replace(tzinfo=NY)
            if now < opened+timedelta(minutes=1) or now >= closed-timedelta(minutes=5):
                cancel_owned(ledger, broker)
                state['last_success'] = now.isoformat()
                ledger.save(state)
                return 'outside_execution_window'
            active = ledger.db.execute("SELECT * FROM orders WHERE status NOT IN ('filled','canceled','expired','rejected')").fetchall()
            if active and (state['session'] != session or any((now-instant(r['created'])).total_seconds() > policy.max_order_age_seconds for r in active)):
                cancel_owned(ledger, broker)
                raise SafetyStop('Unfilled order timed out; cancellation requested.')
            if state['session'] != session:
                if state['session']:
                    elapsed = expected_sessions(state['session'], session)
                    if len(elapsed) != 1 or not state['buy_done']:
                        raise SafetyStop('A strategy session was missed or incomplete; manual review required.')
                    equity_at_open = state['expected_cash']-state['reserve_cash'] + state['expected_qty']*state['reference_open']
                    state['capture'] = state['expected_qty'] > 1e-8 and equity_at_open >= state['cycle_start']*(1+policy.capture_pct/100)
                    excess = max(0, state['expected_qty']*state['reference_open']-state['target'])
                    state['sell_qty'] = state['expected_qty'] if state['capture'] else min(state['expected_qty'], excess/state['reference_open'])
                bar = broker.daily_bar(policy.symbol, session)
                if instant(bar['timestamp']).astimezone(NY).date().isoformat() != session:
                    raise SafetyStop('Opening bar is from the wrong session.')
                reference = finite(bar['open'])
                if reference <= 0:
                    raise SafetyStop('Invalid session opening price.')
                state.update(session=session, reference_open=reference, sell_done=state['sell_qty'] <= 1e-8, buy_done=False)
                state['target'] *= 1+policy.va_pct/100
                state['day_start_equity'] = state.get('last_equity', state['expected_cash']-state['reserve_cash'] + state['expected_qty']*reference)
                ledger.save(state)
            quote = broker.latest_quote(policy.symbol)
            now = fixed_now or datetime.now(UTC)
            bid, ask = quote_check(quote, now, policy)
            equity = state['expected_cash']-state['reserve_cash']+state['expected_qty']*bid
            state['peak'] = max(state['peak'], equity)
            state['last_equity'] = equity
            ledger.save(state)
            observation = {'session': session, 'reference_open': state['reference_open'], 'bid': bid, 'ask': ask,
                           'equity': equity, 'cash': state['expected_cash']-state['reserve_cash'],
                           'shares': state['expected_qty'], 'target': state['target'], 'cycle_start': state['cycle_start'],
                           'cost_basis': state['cost_basis'],
                           'feed': policy.feed, 'quote_timestamp': quote['timestamp'],
                           'execution_sha256': state['execution_sha256'], 'plan_sha256': state['evidence_plan']['plan_sha256'],
                           'policy_sha256': canonical_digest(state['policy']),
                           'phase': 'awaiting_fill' if active else 'session_complete' if state['buy_done'] else 'before_order'}
            ledger.db.execute('INSERT INTO observations(timestamp,payload) VALUES (?,?)', (now.isoformat(), json.dumps(observation, allow_nan=False)))
            ledger.db.commit()
            if equity <= state['peak']*(1-policy.max_drawdown_pct/100) or equity <= state['day_start_equity']*(1-policy.max_daily_loss_pct/100):
                raise SafetyStop('Portfolio drawdown or daily-loss limit reached; pause, without forced liquidation.')
            if active:
                state['last_success'] = now.isoformat()
                ledger.save(state)
                return 'awaiting_fill'
            if not state['sell_done']:
                result = submit_leg(ledger, broker, 'sell', quote, now, closed-timedelta(minutes=5))
            elif not state['buy_done']:
                result = submit_leg(ledger, broker, 'buy', quote, now, closed-timedelta(minutes=5))
            else:
                result = 'session_complete'
            state = ledger.state()
            state['last_success'] = now.isoformat()
            ledger.save(state)
            return result
        except Exception as error:
            ledger.db.rollback()
            # Persist a stop for every uncertain state. No retry loop sends another order.
            reason = str(error) if isinstance(error, SafetyStop) else 'Execution stopped after ' + type(error).__name__ + '; inspect local diagnostics.'
            cancel_owned(ledger, broker)
            ledger.halt(reason)


def status(ledger, *, now=None):
    now = now or datetime.now(UTC)
    state = ledger.state()
    age = (now-instant(state['last_success'])).total_seconds() if state['last_success'] else None
    return {'mode': state['mode'], 'symbol': state['policy']['symbol'], 'session': state['session'],
            'halted': state['halted'], 'stop_requested': ledger.stop_path.exists(),
            'heartbeat_age_seconds': age, 'healthy': not state['halted'] and not ledger.stop_path.exists() and age is not None and 0 <= age <= 180,
            'orders': [dict(row) for row in ledger.db.execute('SELECT client_id,status,filled_qty FROM orders')],
            'last_events': [dict(row) for row in ledger.db.execute('SELECT timestamp,level,message FROM events ORDER BY id DESC LIMIT 10')]}



def acknowledge_cash(ledger, broker, *, kind, expected_delta, reason):
    if kind not in {'income', 'expense', 'external'} or not reason.strip():
        raise ValueError('Classify the reviewed cash event and supply an audit reason.')
    expected_delta = finite(expected_delta)
    if (kind == 'income' and expected_delta <= 0) or (kind == 'expense' and expected_delta >= 0):
        raise ValueError('Income must be positive and expense negative.')
    with ledger.lock():
        account, _ = reconcile(ledger, broker, reviewed=True, allow_cash_difference=True)
        if broker.list_orders(status='open'):
            raise SafetyStop('Wait for all orders to finish before accepting a cash adjustment.')
        state = ledger.state()
        delta = finite(account['cash'])-state['expected_cash']
        if abs(delta-expected_delta) > .02:
            raise SafetyStop('Broker difference does not match the explicitly reviewed amount.')
        state['expected_cash'] = finite(account['cash'])
        if kind == 'external':
            state['reserve_cash'] += delta  # Deposits/withdrawals do not inflate strategy returns.
            if state['reserve_cash'] < 0:
                raise SafetyStop('Withdrawal consumes strategy capital; start a separately reviewed allocation.')
        state['halted'] = 'Cash event acknowledged; review and resume explicitly.'
        ledger.save(state)
        ledger.event('warning', f'Accepted reviewed {kind} cash adjustment {delta:.2f}: {reason}')


def resume_after_review(ledger, broker, *, reason, acknowledge_incomplete=False):
    if not reason.strip():
        raise ValueError('A review reason is required.')
    with ledger.lock():
        reconcile(ledger, broker, reviewed=True)
        if ledger.db.execute("SELECT 1 FROM orders WHERE status NOT IN ('filled','canceled','expired','rejected')").fetchone():
            raise SafetyStop('Unresolved orders prevent resume.')
        state = ledger.state()
        if state.get('execution_sha256') != execution_digest():
            raise SafetyStop('Code changed; the frozen paper experiment cannot be resumed.')
        failed = ledger.db.execute("SELECT 1 FROM orders WHERE session=? AND status IN ('canceled','expired','rejected')", (state['session'],)).fetchone()
        if failed and not acknowledge_incomplete:
            raise SafetyStop('Use --acknowledge-incomplete-session after reviewing all fills; no order is resubmitted.')
        if acknowledge_incomplete:
            from .calendar import latest_completed_session, expected_sessions
            latest = latest_completed_session().isoformat()
            if state['session'] and latest > state['session']:
                policy = Policy(**state['policy'])
                elapsed = len(expected_sessions(state['session'], latest))
                bar = broker.daily_bar(policy.symbol, latest)
                state['target'] *= (1+policy.va_pct/100)**elapsed
                state['session'], state['reference_open'] = latest, finite(bar['open'])
            state['sell_done'] = state['buy_done'] = True
        state['halted'] = None
        ledger.save(state)
        ledger.stop_path.unlink(missing_ok=True)
        ledger.event('warning', 'Operator resumed after review; skipped incomplete session=' + str(acknowledge_incomplete) + ': ' + reason)



def load_credentials(path):
    path = Path(path)
    if not path.exists():
        return
    if path.stat().st_mode & 0o077:
        raise ValueError('Credential file must be private: chmod 600 ' + str(path))
    permitted = {'APCA_API_KEY_ID', 'APCA_API_SECRET_KEY', 'APCA_API_BASE_URL'}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        key, sep, value = line.partition('=')
        if not sep or key not in permitted:
            raise ValueError('Credential file accepts only APCA_API_KEY_ID, APCA_API_SECRET_KEY, APCA_API_BASE_URL.')
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if value:
            os.environ[key] = value


def read_status(path):
    """Read-only view for dashboard and health checks; never creates a ledger."""
    path = Path(path)
    if not path.exists():
        return {'mode': 'alpaca-paper-only', 'initialized': False, 'healthy': False,
                'message': 'Paper account is not initialized; credentials and prospective evidence are pending.'}
    ledger = object.__new__(Ledger)
    ledger.path = path
    ledger.stop_path = Path(str(path)+'.stop')
    ledger.db = sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True, timeout=2)
    ledger.db.row_factory = sqlite3.Row
    try:
        result = status(ledger)
        result['initialized'] = True
        result.pop('orders', None)
        return result
    finally:
        ledger.db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ledger', default='data/paper.sqlite3')
    parser.add_argument('--env-file', default='.env.paper', help='Private KEY=value file; never evaluated as shell code')
    sub = parser.add_subparsers(dest='command', required=True)
    init = sub.add_parser('init', help='Bind an empty dedicated Alpaca paper account; submits no orders')
    init.add_argument('--config', required=True)
    sub.add_parser('status')
    sub.add_parser('step', help='Run one paper execution tick; may submit paper orders')
    run = sub.add_parser('run', help='Run paper ticks with persistent safeguards')
    run.add_argument('--interval', type=int, default=15)
    sub.add_parser('stop', help='Persist emergency stop and cancel owned orders; does not liquidate')
    resume = sub.add_parser('resume', help='Reconcile and clear a reviewed temporary stop')
    resume.add_argument('--reason', required=True)
    resume.add_argument('--acknowledge-incomplete-session', action='store_true')
    cash = sub.add_parser('acknowledge-cash', help='Accept a verified broker cash event; leaves runner halted')
    cash.add_argument('--kind', choices=['income', 'expense', 'external'], required=True)
    cash.add_argument('--expected-delta', type=float, required=True)
    cash.add_argument('--reason', required=True)
    args = parser.parse_args(argv)
    if args.command == 'status':
        try:
            result = read_status(args.ledger)
            print(json.dumps(result, indent=2))
            return 0 if result['healthy'] else 1
        except Exception:
            print(json.dumps({'healthy': False, 'error': 'Cannot read paper ledger status.'}))
            return 1
    ledger = Ledger(args.ledger)
    try:
        if args.command == 'stop':
            # Stop file is set even if credentials are unavailable.
            ledger.stop_path.write_text('Emergency stop requested ' + stamp() + '\n')
        load_credentials(args.env_file)
        from .broker import AlpacaPaperBroker
        policy = Policy(**json.loads(Path(args.config).read_text())) if args.command == 'init' else Policy(**ledger.state()['policy'])
        broker = AlpacaPaperBroker(feed=policy.feed)
        if args.command == 'init':
            initialize(ledger, broker, policy)
            print(json.dumps({'initialized': True, 'mode': 'alpaca-paper-only', 'orders_sent': 0}))
        elif args.command == 'stop':
            if not stop(ledger, broker):
                return 1
        elif args.command == 'resume':
            resume_after_review(ledger, broker, reason=args.reason, acknowledge_incomplete=args.acknowledge_incomplete_session)
        elif args.command == 'acknowledge-cash':
            acknowledge_cash(ledger, broker, kind=args.kind, expected_delta=args.expected_delta, reason=args.reason)
        elif args.command == 'step':
            print(json.dumps({'result': step(ledger, broker)}))
        else:
            if not 5 <= args.interval <= 60:
                raise ValueError('Polling interval must be 5–60 seconds.')
            def on_termination(_signum, _frame):
                raise KeyboardInterrupt
            signal.signal(signal.SIGTERM, on_termination)
            while True:
                print(json.dumps({'result': step(ledger, broker)}), flush=True)
                time.sleep(args.interval)
        return 0
    except KeyboardInterrupt:
        ledger.db.rollback()
        stop(ledger, locals().get('broker'))
        return 130
    except Exception as error:
        message = str(error) if isinstance(error, (SafetyStop, ValueError)) else type(error).__name__ + '; see paper setup guide.'
        print(json.dumps({'error': message, 'mode': 'paper-only'}), file=sys.stderr)
        return 1
    finally:
        ledger.db.close()


if __name__ == '__main__':
    raise SystemExit(main())
