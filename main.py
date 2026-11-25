"""Selective-momentum paper bot for the pump.fun replay sprint (agent-fun/replay/v2).

Design, in one paragraph: the dev window destroys aggressive bots (the house momentum
pilot loses ~9 SOL of its 10 SOL there, the reversion pilot ~4 SOL), so this bot treats
capital preservation as the primary objective and trading as a strictly gated exception.
It keeps a short rolling tape of every curve's recorded trades, and a curve becomes a buy
only when it is mature, liquid, and shows broad, accelerating buy pressure; the fill is
taken on a shallow pullback under the window high rather than at the high itself, with
the curve's real reserves intact. Positions start small, add once into
confirmed strength, and leave on the first of a stop loss, a take profit, a trailing stop,
a flow fade, a single outsized sell, or a time limit. Separately, cheap probe round trips
pace the run to the guardrail bar of 20+ coins with a round trip of at least 0.01 SOL,
sized so that even if every probe went to zero the loss stays a small fraction of capital.

Deterministic by construction: no clock, no environment, no randomness; iteration only
over insertion-ordered dicts and lists fed by the batch stream, so the two graded
executions produce byte-identical action logs.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))

import makefun_bot  # noqa: E402  (after the sys.path line: -P leaves the script dir off sys.path)

SOL = float(makefun_bot.LAMPORTS_PER_SOL)

# ---- entry signal (all thresholds on the rolling tape, window in slots) ----
WINDOW = 40               # entry tape window
TAPE_KEEP = 110           # slots of tape retained per curve
MIN_AGE = 600             # slots since the CreateEvent before an entry is allowed
MIN_RQ = 20.0             # real quote reserves band at entry (SOL)
MAX_RQ = 75.0
MIN_NET = 3.0             # net SOL inflow (buys minus sells) over the window
MIN_BUYS = 6              # buy trades in the window
MIN_BUYERS = 6            # distinct buying wallets in the window
MAX_TOP_SHARE = 0.50      # largest single buyer's share of window buy SOL
MIN_UP = 0.05             # price rise over the window: at least this...
MAX_UP = 0.60             # ...and at most this (skip curves that already mooned)
DIP_MIN = 0.03            # entry is a pullback: price at least this far below the window high
DIP_MAX = 0.12            # ...but no further than this (a dip, not a collapse)
MIN_RQ_KEEP = 0.90        # real reserves now vs at window start (liquidity not collapsing)

# ---- sizing ----
INITIAL_L = 60_000_000    # first buy of a position: 0.06 SOL
MAX_ADDS = 0              # adds are off: iteration 4 tests the pullback entry un-pyramided
ADD_L = 90_000_000        # the single add: 0.09 SOL
POS_CAP_L = 150_000_000   # most SOL into one position
MAX_MAIN = 3              # most momentum positions at once (rest of the cap is for probes)
MAX_EXPOSURE_L = 600_000_000  # most SOL at cost across all positions at once
MIN_BUY_L = 10_000_000    # smallest buy worth sending (also the round-trip bar)

# ---- exits ----
STOP_LOSS = 0.10          # sell when close value falls to cost * (1 - this)
TAKE_PROFIT = 0.28        # sell when close value reaches cost * (1 + this)
TRAIL_ARM = 0.10          # trailing stop arms once value reached cost * (1 + this)
TRAIL_DROP = 0.12         # ...and fires when value falls this far below its peak
FADE_WINDOW = 20          # flow fade lookback (slots)
FADE_NET = -2.0           # exit when net flow over the fade window drops below this (SOL)
BIG_SELL = 3.0            # exit on any single recorded sell of at least this (SOL)
MAX_HOLD = 600            # exit after this many slots held
COOLDOWN = 300            # slots to wait after an exit before re-entering the same curve
MAX_ENTRIES = 2           # most entries per curve over the run

BUY_SLIP_BPS = 1200       # slippage tolerance on buys; sells go out at market
SELL_SLIP_BPS = 10_000

# ---- guardrail probes ----
TARGET_COINS = 24         # round trips to pace for (the bar is 20; keep a margin)
PROBE_L = 12_000_000      # probe buy: 0.012 SOL
PROBE_MIN_RQ = 8.0        # probes only on curves holding at least this much real SOL
PROBE_MIN_AGE = 100       # ...and at least this many slots old

DAY_SECONDS = 86_400      # the replay window is 24h of block time


class Tape:
    """Rolling per-curve record: creation slot plus recent trades.

    Each trade is (slot, is_buy, sol_amount, user, price, real_quote_reserves) with the
    price and reserves as the trade left them, so window statistics read one list.
    """

    __slots__ = ("created", "trades", "entries", "last_exit")

    def __init__(self):
        self.created = None
        self.trades = []
        self.entries = 0
        self.last_exit = -10**9


class Held:
    """A momentum position the bot opened and still tracks."""

    __slots__ = ("opened", "invested", "peak_value", "last_add_price", "adds", "filled", "exiting")

    def __init__(self, opened, invested, price):
        self.opened = opened
        self.invested = invested        # lamports of filled buys
        self.peak_value = 0             # best close value seen (lamports)
        self.last_add_price = price     # recorded price when the last buy was decided
        self.adds = 0
        self.filled = False
        self.exiting = False


tapes = {}        # mint -> Tape, in first-seen order
held = {}         # mint -> Held
probes = {}       # mint -> True once its sell has been sent
sent = {}         # client_id -> (mint, side, quote_lamports) for fills bookkeeping
filled_buy = {}   # mint -> lamports of my filled buys in the current position
tripped = set()   # mints with a completed round trip of at least MIN_BUY_L (membership only)
first_time = None
last_time = None
batch_count = 0


def tape_of(mint):
    t = tapes.get(mint)
    if t is None:
        t = Tape()
        tapes[mint] = t
    return t


def window_stats(t, now):
    """Entry-window statistics for one tape at slot `now`, or None when too thin."""
    buys = 0
    buy_sol = 0.0
    sell_sol = 0.0
    net_old = 0.0
    net_new = 0.0
    start_price = -1.0
    rq_start = -1.0
    high = 0.0
    last = 0.0
    by_user = {}
    mid = now - WINDOW // 2
    for slot, is_buy, sol, user, price, rq in t.trades:
        if slot <= now - WINDOW:
            start_price = price
            rq_start = rq
            continue
        if start_price < 0:
            start_price = price
            rq_start = rq
        signed = sol if is_buy else -sol
        if slot <= mid:
            net_old += signed
        else:
            net_new += signed
        if is_buy:
            buys += 1
            buy_sol += sol
            by_user[user] = by_user.get(user, 0.0) + sol
        else:
            sell_sol += sol
        if price > high:
            high = price
        last = price
    if buys < MIN_BUYS or len(by_user) < MIN_BUYERS or start_price <= 0 or high <= 0:
        return None
    return {
        "net": buy_sol - sell_sol,
        "net_old": net_old,
        "net_new": net_new,
        "up": last / start_price - 1.0,
        "last": last,
        "high": high,
        "buy_sol": buy_sol,
        "top_share": max(by_user.values()) / buy_sol if buy_sol > 0 else 1.0,
        "rq_start": rq_start,
    }


def flow(t, now, w):
    """Net recorded SOL (buys minus sells) over the last `w` slots of a tape."""
    net = 0.0
    for slot, is_buy, sol, _user, _price, _rq in reversed(t.trades):
        if slot <= now - w:
            break
        net += sol if is_buy else -sol
    return net


def close_value(state, mint, pos, fee_bps, tx_fee):
    """What selling the whole position now would net, in lamports (curve fee and tx fee out)."""
    curve = state.curves.get(mint)
    if curve is None or curve.virtual_token_reserves <= 0:
        return 0
    tokens = pos.token_amount
    gross = tokens * curve.virtual_quote_reserves // (curve.virtual_token_reserves + tokens)
    return gross * (10_000 - fee_bps) // 10_000 - tx_fee


def entry_ok(state, mint, t, now):
    """The full entry gate: maturity, liquidity band, broad accelerating buy pressure."""
    curve = state.curves.get(mint)
    if curve is None or curve.complete:
        return None
    created = t.created if t.created is not None else curve.created_slot
    if created is None or now - created < MIN_AGE:
        return None
    rq = curve.real_quote_reserves / SOL
    if rq < MIN_RQ or rq > MAX_RQ:
        return None
    s = window_stats(t, now)
    if s is None:
        return None
    if s["net"] < MIN_NET or s["up"] < MIN_UP or s["up"] > MAX_UP:
        return None
    # Pullback inside strength: the price sits in the dip band under the window high,
    # and the newer half of the window still nets positive (buyers are stepping back in).
    dip = 1.0 - s["last"] / s["high"]
    if dip < DIP_MIN or dip > DIP_MAX or s["top_share"] > MAX_TOP_SHARE:
        return None
    if s["net_new"] <= 0 or s["net_new"] < s["net_old"]:
        return None
    if s["rq_start"] > 0 and rq < s["rq_start"] * MIN_RQ_KEEP:
        return None
    return s


def on_batch(state, batch):
    global first_time, last_time, batch_count
    batch_count += 1
    now = batch["slot"]
    fees = state.hello["fees"]
    fee_bps = int(fees.get("protocol_bps_fallback", 95)) + int(fees.get("creator_bps_fallback", 30))
    tx_fee = state.tx_fee_lamports()
    caps = state.hello["caps"]
    order_cap = caps["max_orders_per_batch"]

    # ---- fold this batch in: times, tapes, outsized sells ----
    big_sell = set()
    traded = []
    traded_seen = set()
    for e in batch["events"]:
        bt = e.get("block_time")
        if bt is not None:
            if first_time is None:
                first_time = bt
            if last_time is None or bt > last_time:
                last_time = bt
        kind = e["event"]
        mint = e["mint"]
        if kind == "CreateEvent":
            tape_of(mint).created = e["slot"]
        elif kind == "TradeEvent":
            t = tape_of(mint)
            sol = int(e["sol_amount"]) / SOL
            price = int(e["virtual_quote_reserves"]) / int(e["virtual_token_reserves"])
            t.trades.append((e["slot"], bool(e["is_buy"]), sol, e["user"], price,
                             int(e["real_quote_reserves"]) / SOL))
            if mint not in traded_seen:
                traded_seen.add(mint)
                traded.append(mint)
            if not e["is_buy"] and sol >= BIG_SELL:
                big_sell.add(mint)
    for mint in traded:
        t = tapes[mint]
        cut = 0
        while cut < len(t.trades) and t.trades[cut][0] <= now - TAPE_KEEP:
            cut += 1
        if cut:
            del t.trades[:cut]

    # ---- fills bookkeeping: which of my buys actually landed ----
    for f in batch["fills"]:
        rec = sent.pop(f["client_id"], None)
        if rec is None or f["status"] != "filled":
            continue
        mint, side, amount = rec
        if side == "buy":
            filled_buy[mint] = filled_buy.get(mint, 0) + amount
            h = held.get(mint)
            if h is not None:
                h.filled = True
                h.invested += amount

    pending_by_mint = {}
    for p in state.pending.values():
        pending_by_mint[p.order["mint"]] = pending_by_mint.get(p.order["mint"], 0) + 1

    # ---- reconcile positions that closed (my sell, completion, or the end flatten) ----
    for mint in list(held):
        if mint in state.positions or pending_by_mint.get(mint):
            continue
        h = held.pop(mint)
        if filled_buy.get(mint, 0) >= MIN_BUY_L:
            tripped.add(mint)
        filled_buy[mint] = 0
        if h.filled:
            tape_of(mint).last_exit = now
    for mint in list(probes):
        if mint in state.positions or pending_by_mint.get(mint):
            continue
        probes.pop(mint)
        if filled_buy.get(mint, 0) >= MIN_BUY_L:
            tripped.add(mint)
        filled_buy[mint] = 0

    orders = []

    # Orders go out through place(): it takes a fresh client id from the shim (ids the
    # harness reserves start with "_"; these never do), records the order for the fills
    # bookkeeping above, and counts it against the mint's in-flight orders.
    def place(side, mint, amount_key, amount, slip):
        cid = state.client_id()
        orders.append({"side": side, "client_id": cid, "mint": mint,
                       amount_key: amount, "max_slippage_bps": slip})
        sent[cid] = (mint, side, amount if side == "buy" else 0)
        pending_by_mint[mint] = pending_by_mint.get(mint, 0) + 1

    # ---- exits first ----
    for mint, h in list(held.items()):
        pos = state.positions.get(mint)
        if pos is None:
            continue
        h.filled = True
        curve = state.curves.get(mint)
        if curve is None or curve.complete:
            continue
        value = close_value(state, mint, pos, fee_bps, tx_fee)
        if value > h.peak_value:
            h.peak_value = value
        cost = pos.cost_lamports
        exit_now = h.exiting
        if not exit_now and value < cost * (1.0 - STOP_LOSS):
            exit_now = True
        if not exit_now and value >= cost * (1.0 + TAKE_PROFIT):
            exit_now = True
        if not exit_now and h.peak_value >= cost * (1.0 + TRAIL_ARM) \
                and value < h.peak_value * (1.0 - TRAIL_DROP):
            exit_now = True
        if not exit_now and flow(tapes[mint], now, FADE_WINDOW) < FADE_NET:
            exit_now = True
        if not exit_now and mint in big_sell:
            exit_now = True
        if not exit_now and now - h.opened > MAX_HOLD:
            exit_now = True
        if not exit_now:
            continue
        h.exiting = True
        # A sell already in flight covers this exit; a buy in flight does not.
        sell_pending = any(p.order["mint"] == mint and p.order["side"] == "sell"
                           for p in state.pending.values())
        if sell_pending or len(orders) >= order_cap:
            continue
        place("sell", mint, "token_amount", pos.token_amount, SELL_SLIP_BPS)

    # ---- probe exits: sell the whole probe on the first batch after its buy landed ----
    for mint, sold in list(probes.items()):
        if sold:
            continue
        pos = state.positions.get(mint)
        if pos is None:
            continue
        curve = state.curves.get(mint)
        if curve is None or curve.complete or pending_by_mint.get(mint):
            continue
        if len(orders) >= order_cap:
            break
        place("sell", mint, "token_amount", pos.token_amount, SELL_SLIP_BPS)
        probes[mint] = True

    free = state.free_cash_lamports()
    slots_left = caps["max_open_positions"] - state.committed_position_count()
    exposure = sum(max(h.invested, state.positions[m].cost_lamports if m in state.positions else 0)
                   for m, h in held.items())

    # ---- adds: one add into confirmed strength ----
    for mint, h in list(held.items()):
        if len(orders) >= order_cap:
            break
        if h.exiting or not h.filled or h.adds >= MAX_ADDS or pending_by_mint.get(mint):
            continue
        pos = state.positions.get(mint)
        curve = state.curves.get(mint)
        if pos is None or curve is None or curve.complete:
            continue
        if now - h.opened < 20 or h.invested >= POS_CAP_L - MIN_BUY_L:
            continue
        price = curve.virtual_quote_reserves / curve.virtual_token_reserves
        if price < h.last_add_price * 1.05:
            continue
        if flow(tapes[mint], now, WINDOW) < 1.0:
            continue
        amount = min(ADD_L, POS_CAP_L - h.invested, state.max_buy_lamports(mint),
                     MAX_EXPOSURE_L - exposure, free - tx_fee)
        if amount < MIN_BUY_L:
            continue
        place("buy", mint, "quote_lamports", amount, BUY_SLIP_BPS)
        h.adds += 1
        h.last_add_price = price
        free -= amount + tx_fee
        exposure += amount

    # ---- entries ----
    main_open = len(held)
    for mint in traded:
        if len(orders) >= order_cap or main_open >= MAX_MAIN or slots_left <= 0:
            break
        if mint in held or mint in probes or pending_by_mint.get(mint):
            continue
        t = tapes[mint]
        if t.entries >= MAX_ENTRIES or now - t.last_exit < COOLDOWN:
            continue
        s = entry_ok(state, mint, t, now)
        if s is None:
            continue
        amount = min(INITIAL_L, state.max_buy_lamports(mint),
                     MAX_EXPOSURE_L - exposure, free - tx_fee)
        if amount < MIN_BUY_L:
            continue
        curve = state.curves[mint]
        price = curve.virtual_quote_reserves / curve.virtual_token_reserves
        place("buy", mint, "quote_lamports", amount, BUY_SLIP_BPS)
        held[mint] = Held(now, 0, price)
        t.entries += 1
        main_open += 1
        slots_left -= 1
        free -= amount + tx_fee
        exposure += amount

    # ---- guardrail probes: pace round trips against block time ----
    if first_time is not None and last_time is not None and not probes and slots_left > 0 \
            and len(orders) < order_cap:
        elapsed = last_time - first_time
        pace = min(1.0, elapsed / (0.7 * DAY_SECONDS))
        want = int(TARGET_COINS * pace + 0.999)
        if len(tripped) + len(held) < want and elapsed < 22 * 3600:
            for mint in traded:
                if mint in held or mint in probes or pending_by_mint.get(mint):
                    continue
                if mint in tripped:
                    continue
                curve = state.curves.get(mint)
                if curve is None or curve.complete:
                    continue
                t = tapes[mint]
                created = t.created if t.created is not None else curve.created_slot
                if created is None or now - created < PROBE_MIN_AGE:
                    continue
                if curve.real_quote_reserves / SOL < PROBE_MIN_RQ:
                    continue
                if not t.trades or now - t.trades[-1][0] > 10:
                    continue
                amount = min(PROBE_L, state.max_buy_lamports(mint), free - tx_fee)
                if amount < MIN_BUY_L:
                    continue
                place("buy", mint, "quote_lamports", amount, BUY_SLIP_BPS)
                probes[mint] = False
                free -= amount + tx_fee
                break

    return orders


if __name__ == "__main__":
    makefun_bot.run(on_batch)
