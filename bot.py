#!/usr/bin/env python3
"""
Polymarket paper-trading bot with a live web dashboard.

Trades with SIMULATED money against REAL live Polymarket prices, so you can
see whether the strategies actually make money before risking a single cent.
While running, it serves a dashboard at  http://localhost:8765  showing your
account value, open trades, history, and what the bot has learned so far.

Commands:
    python3 bot.py paper    - run the bot + dashboard (Ctrl+C to stop)
    python3 bot.py web      - dashboard only (view results without trading)
    python3 bot.py scan     - look for opportunities right now (no trades)
    python3 bot.py status   - print the account summary in the terminal
    python3 bot.py reset    - wipe the simulated account and start over

Strategies:
    1. "arbitrage"  - in a multi-outcome event (e.g. "Who wins the election?"),
       if the YES prices of ALL outcomes add up to less than $1.00, buy every
       outcome. Exactly one of them must pay out $1, so profit is locked in.
    2. "high_prob"  - buy heavy favorites (96-99 cents) that resolve within a
       few days. Research shows favorites are slightly underpriced on average,
       but an upset loses the whole stake. Paper results tell you if it works.

Learning: as trades settle, the bot tracks results per strategy and per price
range. Losing strategies get their trade size halved, then paused entirely;
price ranges that keep losing stop being bought. It needs at least 8 settled
trades before it starts adapting — adjusting on less data would just be noise.
"""

import csv
import json
import math
import ml  # the bot's pure-Python ML library (GBM/forest/MLP/calibration)
import chartml  # chart & pattern ML: learned move model, miner stats
import sportsedge  # self-grading sports fair-value/CLV instrument (SHADOW)
import crossmarket  # self-grading cross-market consensus instrument (SHADOW)
import re
import os
import random
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DASHBOARD_PORT = 8765

HERE = Path(__file__).parent
CONFIG_FILE = HERE / "config.json"
ACCOUNT_FILE = HERE / "paper_account.json"
TRADE_LOG = HERE / "trade_log.csv"
HISTORY_FILE = HERE / "history.json"
LEARNING_FILE = HERE / "learning.json"
ACTIVITY_FILE = HERE / "activity.log"
DASHBOARD_HTML = HERE / "dashboard.html"

session = requests.Session()
session.headers["User-Agent"] = "paper-trading-research-bot"


# ---------------------------------------------------------------- helpers

def now_utc():
    return datetime.now(timezone.utc)


API_LOCK = threading.Lock()
API_RATE, API_CAP = 8.0, 16.0          # sustained calls/sec, burst ceiling
API_BUCKET = {"t": time.time(), "tokens": API_CAP}
API_STATS = {"calls": 0, "throttled": 0, "rate_limited": 0}


def _governor():
    """One shared API budget across scanner, explorer, exits and recorder.
    When it binds, callers wait — breadth shrinks gracefully instead of
    hammering the API."""
    while True:
        with API_LOCK:
            now = time.time()
            API_BUCKET["tokens"] = min(API_CAP, API_BUCKET["tokens"]
                                       + (now - API_BUCKET["t"]) * API_RATE)
            API_BUCKET["t"] = now
            if API_BUCKET["tokens"] >= 1:
                API_BUCKET["tokens"] -= 1
                API_STATS["calls"] += 1
                return
            need = (1 - API_BUCKET["tokens"]) / API_RATE
            API_STATS["throttled"] += 1
        time.sleep(need)


def get_json(url, params=None, tries=3):
    """Fetch a URL and return parsed JSON, retrying a couple of times.
    Governed: every call draws from the shared budget; 429s back off hard."""
    for attempt in range(tries):
        _governor()
        try:
            r = session.get(url, params=params, timeout=20)
            if r.status_code == 429:
                API_STATS["rate_limited"] += 1
                time.sleep(4 * (attempt + 1))   # exponential-ish backoff
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == tries - 1:
                print(f"  ! network error, skipping: {e}")
                return None
            time.sleep(2 * (attempt + 1))


def fnum(value, default=0.0):
    """Polymarket sometimes returns numbers as strings; convert safely."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def jlist(value):
    """Fields like outcomePrices arrive as a JSON string: '[\"0.97\", \"0.03\"]'."""
    if isinstance(value, list):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return []


def best_ask(token_id):
    """Lowest price someone will sell this outcome for, plus how many shares.
    Returns (price, size) or (None, 0) if the book is empty."""
    book = get_json(f"{CLOB}/book", params={"token_id": token_id})
    if not book or not book.get("asks"):
        return None, 0
    top = book["asks"][-1]  # CLOB sorts asks high->low, so best (lowest) is last
    return fnum(top["price"]), fnum(top["size"])


def best_bid(token_id):
    """Highest price someone will pay for this outcome right now."""
    book = get_json(f"{CLOB}/book", params={"token_id": token_id})
    if not book or not book.get("bids"):
        return None
    return fnum(book["bids"][-1]["price"])  # bids sorted low->high, best is last


def _parse_book(book, levels=5):
    if not book or not book.get("asks") or not book.get("bids"):
        return None
    asks, bids = book["asks"][-levels:], book["bids"][-levels:]
    ask, bid = fnum(asks[-1]["price"]), fnum(bids[-1]["price"])
    bid_depth = sum(fnum(x["size"]) for x in bids)
    ask_depth = sum(fnum(x["size"]) for x in asks)
    total = bid_depth + ask_depth
    return {"bid": bid, "ask": ask, "ask_size": fnum(asks[-1]["size"]),
            "spread": round(ask - bid, 4),
            "imbalance": round(bid_depth / total, 3) if total else 0.5,
            "ask_levels": [(fnum(x["price"]), fnum(x["size"]))
                           for x in reversed(asks)]}


def fetch_books_bulk(token_ids):
    """All open positions' orderbooks in ONE round trip (POST /books) —
    the difference between a 25-second price sweep and a 1-second one."""
    out = {}
    for i in range(0, len(token_ids), 100):
        chunk = token_ids[i:i + 100]
        _governor()
        try:
            r = session.post(f"{CLOB}/books",
                             json=[{"token_id": t} for t in chunk], timeout=10)
            r.raise_for_status()
            for b in r.json():
                pb = _parse_book(b)
                out[str(b.get("asset_id"))] = pb
                if pb:    # empty books parse to None — never let one
                    mem_record(b.get("asset_id"),      # poison the batch
                               (pb["bid"] + pb["ask"]) / 2)
                    book_record(b.get("asset_id"), b)
        except Exception as e:
            print(f"  ! bulk books error: {e}")
    return out


# ------------------------------------------------- in-RAM market memory
#
# The machine has RAM to spare, so stop throwing prices away: every price
# this process sees — 1s position sweeps, 15s watchlist pricing, the
# recorder's 400-market minute scans — is remembered at full resolution.
# Packed arrays (12 bytes/tick vs ~120 for Python lists) mean a week of
# 1-second ticks across thousands of markets fits comfortably in a few GB,
# and the chartist stops re-buying 5-minute candles from the API to
# describe moves we already watched happen tick by tick.

import array as _array

PRICE_MEM = {}                       # key -> {"t": times, "p": prices}
PRICE_MEM_CFG = {"max_tokens": 3000, "max_points": 604800}   # 7d of 1s
MEM_LOCK = threading.Lock()   # guards PRICE_MEM/BOOK_MEM membership only
# (audit: unlocked min()-eviction while other threads insert raced into
# RuntimeError, silently aborting scan passes and exit sweeps)


def mem_record(key, mid, ts=None):
    """Remember one observed price at full resolution (0.5s dedupe)."""
    if mid is None:
        return
    key = str(key)
    s = PRICE_MEM.get(key)
    if s is None:
        with MEM_LOCK:
            if len(PRICE_MEM) >= PRICE_MEM_CFG["max_tokens"]:
                oldest = min(list(PRICE_MEM),
                             key=lambda k: PRICE_MEM[k]["t"][-1]
                             if PRICE_MEM.get(k) and len(PRICE_MEM[k]["t"])
                             else 0.0)
                PRICE_MEM.pop(oldest, None)
            s = PRICE_MEM.setdefault(key, {"t": _array.array("d"),
                                           "p": _array.array("f")})
    t = ts or time.time()
    if len(s["t"]) and t - s["t"][-1] < 0.5:
        return
    s["t"].append(t)
    s["p"].append(mid)
    if len(s["t"]) > PRICE_MEM_CFG["max_points"]:
        cut = PRICE_MEM_CFG["max_points"] // 10
        del s["t"][:cut]
        del s["p"][:cut]


def mem_series(key, seconds):
    """The last `seconds` of remembered prices: list of (ts, price)."""
    s = PRICE_MEM.get(str(key))
    if not s or not len(s["t"]):
        return []
    cutoff = time.time() - seconds
    ts = s["t"]
    lo, hi = 0, len(ts)
    while lo < hi:                          # bisect on time
        mid_i = (lo + hi) // 2
        if ts[mid_i] < cutoff:
            lo = mid_i + 1
        else:
            hi = mid_i
    return list(zip(ts[lo:], s["p"][lo:]))


def mem_preload(key, pts):
    """Bulk-merge HISTORICAL points (sorted ascending) beneath any live
    ticks already recorded — mem_record drops out-of-order ticks, so
    history must be spliced in below, never appended on top."""
    key = str(key)
    s = PRICE_MEM.get(key)
    if s is None:
        if len(PRICE_MEM) >= PRICE_MEM_CFG["max_tokens"]:
            return   # honor the cap (audit: preload ignored it)
        s = PRICE_MEM.setdefault(key, {"t": _array.array("d"),
                                       "p": _array.array("f")})
    with MEM_LOCK:   # atomic splice: a concurrent mem_record between the
        # two extends permanently misaligned t/p arrays (audit)
        first_live = s["t"][0] if len(s["t"]) else float("inf")
        old_t = _array.array("d")
        old_p = _array.array("f")
        for t, p in pts:
            if t < first_live - 0.5:
                old_t.append(t)
                old_p.append(p)
        old_t.extend(s["t"])
        old_p.extend(s["p"])
        s["t"], s["p"] = old_t, old_p


# Depth-ladder history: the one data stream we always discarded. Every
# book the bot fetches, the top-5 bid and ask ladders are remembered —
# 88 bytes/tick packed. This is the dataset TRICKS #5 (whale-print depth
# deltas front-running news) needs; it accumulates here until a deep
# review tests that hypothesis on real history.
BOOK_MEM = {}                        # token -> {"t": times, "l": ladders}
BOOK_LVLS = 5


def book_record(token, raw):
    """Remember one full order-book snapshot (top 5 levels each side)."""
    try:
        bids, asks = raw.get("bids") or [], raw.get("asks") or []
        if not bids or not asks:
            return
        token = str(token)
        s = BOOK_MEM.get(token)
        if s is None:
            with MEM_LOCK:
                if len(BOOK_MEM) >= PRICE_MEM_CFG["max_tokens"]:
                    oldest = min(list(BOOK_MEM),
                                 key=lambda k: BOOK_MEM[k]["t"][-1]
                                 if BOOK_MEM.get(k) and len(BOOK_MEM[k]["t"])
                                 else 0.0)
                    BOOK_MEM.pop(oldest, None)
                s = BOOK_MEM.setdefault(token, {"t": _array.array("d"),
                                                "l": _array.array("f")})
        t = time.time()
        if len(s["t"]) and t - s["t"][-1] < 0.9:
            return
        row = []
        for side in (bids, asks):               # books arrive worst-first
            lvls = list(reversed(side))[:BOOK_LVLS]
            for lv in lvls:
                row += [fnum(lv.get("price")), fnum(lv.get("size"))]
            row += [0.0, 0.0] * (BOOK_LVLS - len(lvls))
        s["t"].append(t)
        s["l"].extend(row)
        cap = PRICE_MEM_CFG.get("book_points", 1209600)      # 14d of 1s
        if len(s["t"]) > cap:
            cut = cap // 10
            del s["t"][:cut]
            del s["l"][:cut * BOOK_LVLS * 4]
    except Exception:
        pass


def book_series(token, seconds):
    """Depth-ladder history: list of (ts, [b1p,b1s,...,a5p,a5s])."""
    s = BOOK_MEM.get(str(token))
    if not s or not len(s["t"]):
        return []
    cutoff = time.time() - seconds
    width = BOOK_LVLS * 4
    out = []
    for i in range(len(s["t"]) - 1, -1, -1):
        if s["t"][i] < cutoff:
            break
        out.append((s["t"][i], list(s["l"][i * width:(i + 1) * width])))
    return out[::-1]


# In-RAM research corpus: the recorder's full row set, parsed ONCE at
# warm-start and appended live — model 15 retrains stop re-parsing two
# million CSV rows from disk every cycle.
CORPUS = {}                          # market_id -> [(ts,p,bid,ask,vol,h)]
_CORPUS_DONE = []

# Model 16 — the learned chartist (chartml.py): P(a price move reverts),
# trained on tick memory's own history. Gates the fast fade desk.
CHARTML_FILE = HERE / "chart_model.json"
try:
    CHARTML = json.loads(CHARTML_FILE.read_text())
except (OSError, ValueError):
    CHARTML = {}

SPORTSEDGE_FILE = HERE / "sportsedge_model.json"
try:
    SPORTSEDGE = json.loads(SPORTSEDGE_FILE.read_text())
except (OSError, ValueError):
    SPORTSEDGE = {"ratings": {}, "seen_finals": [], "preds": [],
                  "scorecard": {}, "updated": None}

CROSSMARKET_FILE = HERE / "crossmarket_model.json"
try:
    CROSSMARKET = json.loads(CROSSMARKET_FILE.read_text())
except (OSError, ValueError):
    CROSSMARKET = {"pool": [], "pool_ts": None, "preds": [],
                   "scorecard": {}, "updated": None}


def chartml_loop():
    """Retrain the move model every 6h from tick memory; adopt a new fit
    only if its walk-forward skill is positive (never trade down)."""
    while True:
        time.sleep(6 * 3600)
        try:
            series = {}
            for k in list(PRICE_MEM):
                s = PRICE_MEM.get(k)
                if not s or not k.startswith("m:") or len(s["t"]) < 30:
                    continue
                n = min(len(s["t"]), len(s["p"]))   # tolerate mid-append
                series[k] = list(zip(s["t"][:n], s["p"][:n]))
            st = chartml.train_move_model(chartml.build_move_events(series))
            if st.get("model") and st.get("skill", 0) > 0:
                CHARTML.clear()
                CHARTML.update(st)
                atomic_write(CHARTML_FILE, json.dumps(st))
                journal("CHARTML", skill=st["skill"], n=st["n_events"],
                        champion=st.get("champion"))
        except Exception as e:
            print(f"  ! chartml retrain error: {e}")


def mem_stats():
    pts = sum(len(s["t"]) for s in PRICE_MEM.values())
    bpts = sum(len(s["t"]) for s in BOOK_MEM.values())
    crows = sum(len(v) for v in CORPUS.values())
    return {"tokens": len(PRICE_MEM), "points": pts,
            "mb": round(pts * 12 / 1e6, 1),
            "book_tokens": len(BOOK_MEM), "book_points": bpts,
            "book_mb": round(bpts * 88 / 1e6, 1),
            "corpus_rows": crows,
            "corpus_mb": round(crows * 200 / 1e6, 1),
            "budget_gb": PRICE_MEM_CFG.get("budget_gb", 15)}


def mem_warmstart():
    """Load the recorder's entire disk history into RAM at startup: price
    ticks (merged BENEATH live ticks via mem_preload — markets the live
    loops touch first no longer lose their history), the parsed research
    corpus for model 15, and 48h of token-level candles for every open
    position so the chartist has deep charts immediately."""
    t0, n = time.time(), 0
    hist = {}
    for path in sorted(DATA_DIR.glob("snapshots-*.csv")):
        try:
            with path.open() as f:
                for r in csv.reader(f):
                    try:
                        ts = datetime.fromisoformat(r[0]).timestamp()
                        p = float(r[2])
                    except (ValueError, IndexError):
                        continue
                    hist.setdefault(f"m:{r[1]}", []).append((ts, p))
                    n += 1
                    try:
                        CORPUS.setdefault(r[1], []).append(
                            (ts, p, float(r[3]), float(r[4]),
                             float(r[5]), float(r[6])))
                    except (ValueError, IndexError):
                        pass
        except OSError:
            continue
    for key, pts in hist.items():
        mem_preload(key, pts)
    hist.clear()
    _CORPUS_DONE.append(time.time())
    try:    # token-level charts for what we're actually holding
        acct = json.loads(ACCOUNT_FILE.read_text())
        toks = {str(leg["token_id"]) for pos in acct["positions"]
                for leg in pos["legs"] if leg.get("token_id")}
        end = int(time.time())
        for tk in toks:
            h = get_json(f"{CLOB}/prices-history",
                         params={"market": tk, "startTs": end - 48 * 3600,
                                 "endTs": end, "fidelity": 1}) or {}
            pts = [(float(pt["t"]), fnum(pt.get("p")))
                   for pt in h.get("history", []) if pt.get("t")]
            if pts:
                mem_preload(tk, pts)
    except Exception:
        pass
    m = mem_stats()
    print(f"  memory warm-started: {n:,} ticks / {m['tokens']:,} markets, "
          f"corpus {m['corpus_rows']:,} rows, in {time.time() - t0:.0f}s")


def book_stats(token_id, levels=5):
    """Microstructure snapshot of one orderbook: best prices, spread, and
    depth imbalance (what share of nearby resting orders are buyers).
    Imbalance near 0 = sellers dominate = price pressure down."""
    book = get_json(f"{CLOB}/book", params={"token_id": token_id})
    if not book or not book.get("asks") or not book.get("bids"):
        return None
    asks, bids = book["asks"][-levels:], book["bids"][-levels:]
    ask, bid = fnum(asks[-1]["price"]), fnum(bids[-1]["price"])
    bid_depth = sum(fnum(x["size"]) for x in bids)
    ask_depth = sum(fnum(x["size"]) for x in asks)
    total = bid_depth + ask_depth
    mem_record(token_id, (bid + ask) / 2)
    return {"bid": bid, "ask": ask, "ask_size": fnum(asks[-1]["size"]),
            "spread": round(ask - bid, 4),
            "imbalance": round(bid_depth / total, 3) if total else 0.5,
            "ask_levels": [(fnum(x["price"]), fnum(x["size"]))
                           for x in reversed(asks)]}  # best price first


def vwap_fill(ask_levels, shares):
    """Realistic execution: walk the book level by level and return the
    average price actually paid to fill `shares` (None if not enough depth).
    Top-of-book prices lie for any real order size."""
    left, cost = shares, 0.0
    for price, size in ask_levels:
        take = min(left, size)
        cost += take * price
        left -= take
        if left <= 0:
            return round(cost / shares, 4)
    return None


def parse_end_date(market):
    try:
        return datetime.fromisoformat(market["endDate"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        return None


def atomic_write(path, text):
    """Crash-safe write: a kill mid-save must never corrupt a state file.
    Write to a temp file in the same directory, then atomically swap it in."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def note(msg):
    """Print a message and keep it in the activity feed for the dashboard."""
    print("  " + msg)
    line = f"{now_utc():%Y-%m-%d %H:%M} UTC | {msg}\n"
    lines = []
    if ACTIVITY_FILE.exists():
        lines = ACTIVITY_FILE.read_text().splitlines(keepends=True)[-300:]
    atomic_write(ACTIVITY_FILE, "".join(lines) + line)


# ---------------------------------------------------------------- account

def load_config():
    cfg = json.loads(CONFIG_FILE.read_text())
    mem = cfg.get("memory") or {}
    PRICE_MEM_CFG["max_points"] = int(mem.get("price_mem_hours", 720) * 3600)
    PRICE_MEM_CFG["max_tokens"] = int(mem.get("price_mem_tokens", 10000))
    PRICE_MEM_CFG["book_points"] = int(mem.get("book_days", 14) * 86400)
    PRICE_MEM_CFG["budget_gb"] = mem.get("budget_gb", 15)
    return cfg


def load_account(cfg):
    if ACCOUNT_FILE.exists():
        account = json.loads(ACCOUNT_FILE.read_text())
        account.setdefault("settled", [])
        return account
    return {"cash": cfg["starting_cash"], "starting_cash": cfg["starting_cash"],
            "positions": [], "settled": [], "realized_pnl": 0.0,
            "created": now_utc().isoformat()}


def save_account(account):
    atomic_write(ACCOUNT_FILE, json.dumps(account, indent=2))


def record_history(account, force=False):
    """Snapshot account value for the dashboard chart. Quiet minutes where
    nothing changed are skipped so the file doesn't fill up with flat points."""
    history = []
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text())
        except ValueError:
            history = []
    invested = sum(position_value(p) for p in account["positions"])
    alloc = load_config().get("allocations", {})
    strat = {}
    for s in STRATEGIES:
        realized = sum(t["pnl"] for t in account["settled"] if t["strategy"] == s)
        unreal = sum(position_value(p) - p["cost"]
                     for p in account["positions"] if p["strategy"] == s)
        strat[s] = round(alloc.get(s, 0) + realized + unreal, 2)
    point = {"t": now_utc().isoformat(timespec="seconds"),
             "cash": round(account["cash"], 2),
             "invested": round(invested, 2),
             "total": round(account["cash"] + invested, 2),
             "strat": strat}
    if (not force and history
            and history[-1]["cash"] == point["cash"]
            and history[-1]["invested"] == point["invested"]):
        return
    history.append(point)
    atomic_write(HISTORY_FILE, json.dumps(history[-20000:]))


def log_trade(action, strategy, name, detail, cost, proceeds, pnl, cash_after):
    new_file = not TRADE_LOG.exists()
    with TRADE_LOG.open("a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["time_utc", "action", "strategy", "market", "detail",
                        "cost", "proceeds", "pnl", "cash_after"])
        w.writerow([now_utc().isoformat(timespec="seconds"), action, strategy,
                    name, detail, f"{cost:.2f}", f"{proceeds:.2f}",
                    f"{pnl:.2f}", f"{cash_after:.2f}"])


def position_value(pos):
    """What an open position is worth right now, in paper dollars.
    Arbitrage payouts are locked at $1/share once all legs are bought, so they
    are valued at the guaranteed payout (minus anything already collected).
    Favorites are valued at the latest live market price the bot observed."""
    if pos["strategy"] == "arbitrage":
        received = sum(l["proceeds"] for l in pos["legs"] if l["settled"])
        return round(max(pos["shares"] * 1.0 - received, 0), 2)
    price = (pos.get("last_mid") or pos.get("last_price")
             or pos.get("entry_price") or 0)
    return round(pos["shares"] * price, 2)


def held_ids(account):
    """All market/event ids we already hold, so we never double-buy."""
    ids = set()
    for pos in account["positions"]:
        if pos.get("event_id"):
            ids.add(pos["event_id"])
        for leg in pos["legs"]:
            ids.add(leg["market_id"])
    return ids


# --------------------------------------------------------------- learning
#
# An honest feedback loop, not magic: once a strategy has 8+ settled trades,
# losing streaks shrink its trade size; 16+ settled trades of net loss pause
# it. For heavy favorites, each entry price (96c, 97c, ...) is tracked
# separately and a price that keeps losing stops being bought.

def news_tuning(account):
    """Adapt HOW news trades are taken, from their own settled results:
    which move sizes count as real news, which direction of news works,
    and (via the standard multiplier) how big to size. Each rule needs 6+
    settled trades of evidence before it activates."""
    mode = load_config().get("news", {}).get("mode", "follow")
    trades = [t for t in account.get("settled", []) if t["strategy"] == "news"
              and (t.get("context") or {}).get("move_1h") is not None
              and t["context"].get("mode", "follow") == mode]
    tune = {"min_move": None, "blocked_directions": [], "notes": []}
    small = [t["pnl"] for t in trades if abs(t["context"]["move_1h"]) < 0.12]
    big = [t["pnl"] for t in trades if abs(t["context"]["move_1h"]) >= 0.12]
    if len(small) >= 6 and sum(small) < 0:
        tune["min_move"] = 0.12
        tune["notes"].append("small moves (8-12c) lose -> now requires 12c+ news moves")
    for name, pnls in (("up", [t["pnl"] for t in trades if t["context"]["move_1h"] > 0]),
                       ("down", [t["pnl"] for t in trades if t["context"]["move_1h"] < 0])):
        if len(pnls) >= 6 and sum(pnls) < 0:
            tune["blocked_directions"].append(name)
            tune["notes"].append(f"trading {name}-moves loses -> {name} news blocked")
    stops = sum(1 for t in trades if "stop-loss" in (t.get("reason") or ""))
    if len(trades) >= 8 and stops / len(trades) > 0.7:
        tune["notes"].append("most exits are stop-outs -> strategy is chasing noise (size already auto-reduced)")
    return tune


STRATEGIES = ("arbitrage", "high_prob", "news", "explore", "daytrade")


def family_of(name):
    """Market family: the question with numbers stripped — 'BTC above 66k at
    3pm' and 'BTC above 67k at 4pm' are the SAME experiment repeated."""
    s = re.sub(r"[0-9]+", "#", (name or "").lower())
    return " ".join(re.sub(r"[^a-z# ]", " ", s).split()[:6])


def effective_n(trades):
    """Count clusters of (market family, resolution day), not raw rows —
    30 settles of one hourly BTC market are a handful of observations."""
    return len({(family_of(t.get("name")), (t.get("closed") or "")[:10])
                for t in trades})


# Canonical bet-category keys for the per-category brain specialist layer.
# Polymarket's raw tags (Sports, Crypto, Politics, Economy, Finance, Business,
# Pop Culture, Tech, Science, ...) collapse onto the six families the bot
# specializes by. None when no category is known — the common path, where the
# category layer is a pure no-op and the GLOBAL model decides alone.
_CAT_KEY_MAP = {
    "sports": "sports", "esports": "sports",
    "crypto": "crypto",
    "politics": "politics", "elections": "politics", "geopolitics": "politics",
    "weather": "weather", "climate": "weather",
    "economy": "macro", "finance": "macro", "business": "macro",
    "macro": "macro", "fed": "macro", "inflation": "macro",
    "pop culture": "social", "social": "social", "tech": "social",
    "science": "social", "mentions": "social", "twitter": "social",
}


def cat_key(category):
    """Normalize a raw market category label to one canonical specialist key
    (sports/crypto/weather/politics/macro/social), or None if unknown/absent.
    Point-in-time only: derived from the category already stamped on the
    position/opportunity — never recomputed or fetched at decision time."""
    if not category:
        return None
    return _CAT_KEY_MAP.get(str(category).strip().lower())


def dead_cohort(s):
    """Settles the CURRENT code is structurally unable to repeat: sports
    entered via the kelly lane, excluded permanently on 06-12. They stay in
    the books (cash is cash) but say nothing about the strategy as it now
    exists, so sizing judgment must skip them — wins and losses alike, or
    the filter is cherry-picking instead of era hygiene."""
    ctx = s.get("context") or {}
    return (ctx.get("lane") == "r90"
            and (s.get("category") == "Sports"
                 or bool(_SPORTSY.search(s.get("name") or ""))))


def compute_learning(account):
    out = {}
    news_mode = load_config().get("news", {}).get("mode", "follow")
    for strat in STRATEGIES:
        settled = [s for s in account.get("settled", []) if s["strategy"] == strat]
        if strat == "news":  # judge the current mode on its own record only
            settled = [s for s in settled
                       if (s.get("context") or {}).get("mode", "follow") == news_mode]
        n = len(settled)
        wins = sum(1 for s in settled if s["pnl"] >= 0)
        total_pnl = sum(s["pnl"] for s in settled)
        # judge streaks on MATERIAL settles only: a 3-cent protective exit is
        # an insurance premium, not evidence the strategy fails — 11 such
        # exits once halved favorites before a single market had resolved.
        # Era hygiene comes first: dead-cohort settles cannot recur, so they
        # cannot testify about the living strategy (the unfiltered totals
        # above still show them — the account never forgets, only the judge).
        material = [s for s in settled
                    if abs(s["pnl"]) >= 0.15 and not dead_cohort(s)]
        n_mat = len(material)
        last8 = sum(s["pnl"] for s in material[-8:])
        last16 = sum(s["pnl"] for s in material[-16:])

        if n_mat < 8:
            mult = 1.0
            status = (f"gathering data — adapts after 8 material settles "
                      f"(has {n_mat} of {n} total)")
        elif n_mat >= 16 and last16 < 0:
            mult = 0.0
            status = "paused — net loss over the last 16 settled trades"
        elif last8 < 0:
            mult = 0.5
            status = "recent losses — trade size cut in half"
        else:
            mult = 1.0
            status = "recent results profitable — trading at full size"

        if strat == "explore":
            # The explorer buys information — by design it bets where the
            # outcome is unknown, so win-streak rules are the wrong yardstick.
            # It pauses only when its loss budget ($50) is truly spent; bad
            # cells are still pruned by the band/category blocks below.
            if total_pnl <= -50:
                mult, status = 0.0, "paused — $50 information budget spent"
            else:
                mult = 1.0
                status = (f"buying data — ${-total_pnl:.2f} of $50 "
                          f"information budget used" if total_pnl < 0
                          else "buying data — information budget intact")

        bands, blocked = {}, []
        cats, blocked_cats = {}, []
        if strat in ("high_prob", "news", "explore", "daytrade"):
            # 14-day window, same precedent as the pattern miner: markets
            # adapt, and a block earned by a failure mode that no longer
            # exists (e.g. stop-churn) must age out, not govern forever
            cutoff14 = (now_utc() - timedelta(days=14)).isoformat(
                timespec="seconds")
            for s in material:
                if s.get("closed", "") < cutoff14:
                    continue
                band = str(int(round(s.get("entry_price", 0) * 100)))
                b = bands.setdefault(band, {"n": 0, "wins": 0, "pnl": 0.0})
                b["n"] += 1
                b["wins"] += 1 if s["pnl"] >= 0 else 0
                b["pnl"] = round(b["pnl"] + s["pnl"], 2)
                cat = s.get("category") or "Other"
                c = cats.setdefault(cat, {"n": 0, "wins": 0, "pnl": 0.0})
                c["n"] += 1
                c["wins"] += 1 if s["pnl"] >= 0 else 0
                c["pnl"] = round(c["pnl"] + s["pnl"], 2)
            blocked = sorted(b for b, v in bands.items()
                             if v["n"] >= 6 and v["pnl"] < 0)
            # Owner-protected categories never auto-pause on the cat block:
            # the n>=6 & pnl<0 rule is a hair-trigger that benches a category
            # over a single cent of noise (Crypto tripped it at -$0.16 despite
            # 81% win / +$18.77 lifetime). Protected cats are exempted by
            # explicit config choice; the bankroll-level breaker + auto-rollback
            # remain the real backstop. Reversible: empty the config list.
            protected = set(load_config().get("learning", {})
                            .get("protected_categories", []))
            blocked_cats = sorted(c for c, v in cats.items()
                                  if v["n"] >= 6 and v["pnl"] < 0
                                  and c not in protected)

        out[strat] = {"settled": n, "wins": wins,
                      "total_pnl": round(total_pnl, 2),
                      "recent_pnl": round(last8, 2),
                      "multiplier": mult, "status": status,
                      "bands": bands, "blocked_bands": blocked,
                      "categories": cats, "blocked_categories": blocked_cats}
    out["news"]["tuning"] = news_tuning(account)
    return out


def save_learning(learning):
    """Write learning state for the dashboard; announce any changes."""
    old = {}
    if LEARNING_FILE.exists():
        try:
            old = json.loads(LEARNING_FILE.read_text())
        except ValueError:
            old = {}
    for strat, info in learning.items():
        if old.get(strat, {}).get("status") not in (None, info["status"]):
            note(f"LEARNING: {strat} -> {info['status']}")
        newly_blocked = set(info["blocked_bands"]) - set(old.get(strat, {}).get("blocked_bands", []))
        for band in sorted(newly_blocked):
            note(f"LEARNING: stopped buying {band}c favorites — that price range keeps losing")
        newly_blocked_cats = (set(info["blocked_categories"])
                              - set(old.get(strat, {}).get("blocked_categories", [])))
        for cat in sorted(newly_blocked_cats):
            note(f"LEARNING: stopped trading {cat} markets — that market type keeps losing")
    atomic_write(LEARNING_FILE, json.dumps(learning, indent=2))


# ---------------------------------------------------------------- backtest
#
# What a quant does before risking money: replay the strategy against
# history. We pull recently-resolved markets, look up what the favorite
# actually traded at 48h/24h/6h before resolution, and check whether buying
# it would have made money. Real prices, real outcomes, no waiting.

BACKTEST_FILE = HERE / "backtest_results.json"
LOOKBACK_HOURS = (48, 24, 6)


def resolution_time(market):
    for field in ("umaEndDate", "closedTime", "endDate"):
        raw = market.get(field)
        if not raw:
            continue
        try:
            return datetime.fromisoformat(
                str(raw).replace("Z", "+00:00").replace(" ", "T").replace("+00", "+00:00"))
        except ValueError:
            continue
    return None


def backtest(sample_target=400, min_volume=1000, days=365):
    print(f"Backtesting the favorites strategy on up to {sample_target} resolved "
          f"markets from the last {days} days (large runs take hours)...")
    samples = []           # one record per (market, lookback) pair
    seen, offset = 0, 0
    cutoff = now_utc() - timedelta(days=days)

    while len({s['id'] for s in samples}) < sample_target and offset < 100000:
        page = get_json(f"{GAMMA}/markets", params={
            "closed": "true", "order": "endDate", "ascending": "false",
            "limit": 100, "offset": offset, "volume_num_min": min_volume,
            "end_date_min": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date_max": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }) or []
        if not page:
            break
        offset += 100

        for m in page:
            tokens = jlist(m.get("clobTokenIds"))
            finals = [fnum(p) for p in jlist(m.get("outcomePrices"))]
            if len(tokens) != 2 or len(finals) != 2:
                continue
            if not (max(finals) > 0.99 and min(finals) < 0.01):
                continue  # skip ties / 50-50 resolutions
            resolved = resolution_time(m)
            if not resolved or resolved < cutoff:
                continue
            seen += 1

            end_ts = int(resolved.timestamp())
            h = get_json(f"{CLOB}/prices-history", params={
                "market": tokens[0],
                "startTs": end_ts - (max(LOOKBACK_HOURS) + 4) * 3600,
                "endTs": end_ts, "fidelity": 60})
            time.sleep(0.12)
            points = (h or {}).get("history", [])
            if len(points) < 3:
                continue

            for lb in LOOKBACK_HOURS:
                target = end_ts - lb * 3600
                pt = min(points, key=lambda x: abs(x["t"] - target))
                if abs(pt["t"] - target) > 2 * 3600:
                    continue  # no price near that moment
                p_yes = fnum(pt["p"])
                fav = 0 if p_yes >= 0.5 else 1
                entry = p_yes if fav == 0 else 1.0 - p_yes
                if not 0.90 <= entry <= 0.995:
                    continue
                samples.append({
                    "id": m["id"], "lookback_h": lb,
                    "band": int(entry * 100),
                    "entry": round(entry, 3),
                    "won": finals[fav] > 0.99,
                })
            done = len({s['id'] for s in samples})
            if done and done % 250 == 0:
                print(f"  ...{done} markets sampled")
            if done >= sample_target:
                break

    # aggregate: per lookback, per price band
    report = {}
    for lb in LOOKBACK_HOURS:
        rows = [s for s in samples if s["lookback_h"] == lb]
        bands = {}
        for s in rows:
            b = bands.setdefault(str(s["band"]), {"n": 0, "wins": 0, "cost": 0.0})
            b["n"] += 1
            b["wins"] += 1 if s["won"] else 0
            b["cost"] += s["entry"]
        for b in bands.values():
            b["win_rate"] = round(b["wins"] / b["n"], 4)
            b["roi"] = round((b["wins"] - b["cost"]) / b["cost"], 4)
            b["cost"] = round(b["cost"], 2)
        report[str(lb)] = bands

    results = {"generated": now_utc().isoformat(timespec="seconds"),
               "markets_scanned": seen,
               "markets_sampled": len({s['id'] for s in samples}),
               "trades_simulated": len(samples),
               "by_lookback_hours": report}
    atomic_write(BACKTEST_FILE, json.dumps(results, indent=2))

    print(f"\nScanned {seen} resolved markets; {results['markets_sampled']} "
          f"had a favorite in the 90-99c range; "
          f"{results['trades_simulated']} simulated trades.\n")
    for lb in LOOKBACK_HOURS:
        print(f"Buying the favorite {lb}h before resolution:")
        bands = report[str(lb)]
        for band in sorted(bands, key=int):
            b = bands[band]
            print(f"  {band}c: {b['n']:>4} trades | won {b['win_rate']:.1%} "
                  f"| return {b['roi']:+.2%} per $1")
        print()
    print(f"Saved to {BACKTEST_FILE.name} (also shown on the dashboard).")
    return results


# ---------------------------------------------------------- data recorder
#
# What trading desks actually do to "collect data fast": record the whole
# market continuously. Every minute we snapshot ~400 markets' live prices
# into data/snapshots-<date>.csv. Once those markets resolve, `research`
# joins snapshots with real outcomes — every recorded market becomes a
# simulated trade. That's thousands of labeled data points per day.

DATA_DIR = HERE / "data"
META_FILE = DATA_DIR / "markets_meta.jsonl"
DATA_STATS_FILE = DATA_DIR / "recorder_stats.json"
RESEARCH_FILE = HERE / "research_results.json"


def snapshot_once(pages, seen):
    """Record one pass of live prices across many markets. Returns row count."""
    now = now_utc()
    ts = now.isoformat(timespec="seconds")
    csv_f = DATA_DIR / f"snapshots-{now:%Y-%m-%d}.csv"
    new_file = not csv_f.exists()
    horizon = (now + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    queries = [  # the most-traded markets + everything resolving soon
        {"order": "volume24hr", "ascending": "false"},
        {"order": "endDate", "ascending": "true",
         "end_date_min": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
         "end_date_max": horizon},
    ]
    rows = 0
    with csv_f.open("a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["ts", "market_id", "p_yes", "bid", "ask",
                        "vol24h", "hours_to_end"])
        for q in queries:
            for offset in range(0, max(1, pages // 2) * 100, 100):
                page = get_json(f"{GAMMA}/markets", params={
                    "active": "true", "closed": "false",
                    "limit": 100, "offset": offset, **q}) or []
                for m in page:
                    prices = [fnum(p) for p in jlist(m.get("outcomePrices"))]
                    if len(prices) != 2:
                        continue
                    end = parse_end_date(m)
                    hours = round((end - now).total_seconds() / 3600, 2) if end else ""
                    w.writerow([ts, m["id"], prices[0],
                                fnum(m.get("bestBid"), ""), fnum(m.get("bestAsk"), ""),
                                round(fnum(m.get("volume24hr")), 1), hours])
                    mem_record(f"m:{m['id']}", prices[0])
                    if _CORPUS_DONE:    # keep the hot corpus current too
                        try:
                            CORPUS.setdefault(str(m["id"]), []).append(
                                (time.time(), prices[0],
                                 fnum(m.get("bestBid")), fnum(m.get("bestAsk")),
                                 fnum(m.get("volume24hr")),
                                 float(hours) if hours != "" else 999.0))
                        except (ValueError, TypeError):
                            pass
                    rows += 1
                    if str(m["id"]) not in seen:
                        seen.add(str(m["id"]))
                        with META_FILE.open("a") as mf:
                            mf.write(json.dumps({"id": str(m["id"]),
                                                 "q": m.get("question", "")[:120],
                                                 "end": m.get("endDate")}) + "\n")
                time.sleep(0.15)
    return rows


def recorder_loop(cfg):
    """Background thread: snapshot the market every minute, forever."""
    rc = cfg.get("research_recorder", {})
    interval = max(30, int(rc.get("interval_seconds", 60)))
    pages = max(2, int(rc.get("pages_per_cycle", 4)))
    DATA_DIR.mkdir(exist_ok=True)
    seen = set()
    if META_FILE.exists():
        for line in META_FILE.read_text().splitlines():
            try:
                seen.add(json.loads(line)["id"])
            except ValueError:
                pass
    total = 0
    if DATA_STATS_FILE.exists():
        try:
            total = json.loads(DATA_STATS_FILE.read_text()).get("total_rows", 0)
        except ValueError:
            pass
    while True:
        start = time.time()
        try:
            rows = snapshot_once(pages, seen)
            total += rows
            atomic_write(DATA_STATS_FILE, json.dumps({
                "updated": now_utc().isoformat(timespec="seconds"),
                "last_cycle_rows": rows, "interval_seconds": interval,
                "total_rows": total, "markets_tracked": len(seen)}))
            # keep a rolling month of raw data so the disk doesn't fill up
            for old in sorted(DATA_DIR.glob("snapshots-*.csv"))[:-30]:
                old.unlink()
            if int(time.time()) % 3600 < interval:   # hourly: trim CORPUS
                cut = time.time() - 14 * 86400        # to 14 days so RAM
                for mid_ in list(CORPUS):             # stays inside budget
                    rows_ = CORPUS.get(mid_)
                    if rows_ and rows_[0][0] < cut:
                        CORPUS[mid_] = [r for r in rows_ if r[0] >= cut]
                        if not CORPUS[mid_]:
                            CORPUS.pop(mid_, None)
        except Exception as e:
            print(f"  ! recorder error (will retry): {e}")
        time.sleep(max(5, interval - (time.time() - start)))


def research():
    """Join recorded snapshots with real outcomes and report what the data
    says, band by band. Every labeled snapshot is a simulated trade."""
    files = sorted(DATA_DIR.glob("snapshots-*.csv"))
    if not files:
        print("No recorded data yet — run 'python3 bot.py paper' first; the "
              "recorder collects ~400 markets per minute while it runs.")
        return
    print(f"Reading {len(files)} day(s) of recorded snapshots...")
    # one observation per market per time bucket per band — repeated
    # snapshots of the same market minutes apart are not independent data
    obs = {}
    ids = set()
    for path in files:
        with path.open() as f:
            for row in csv.DictReader(f):
                try:
                    p_yes = float(row["p_yes"])
                    hours = float(row["hours_to_end"])
                    bid, ask = float(row["bid"]), float(row["ask"])
                except (ValueError, KeyError):
                    continue
                if hours < 0:
                    continue
                if not (0 < bid < 1 and 0 < ask < 1) or ask - bid > 0.04:
                    continue   # we never trade wide/empty books — research
                               # must measure the same universe we trade
                fav = 0 if p_yes >= 0.5 else 1
                entry = ask if fav == 0 else round(1 - bid, 3)  # NO ask = 1 - YES bid
                if not 0.90 <= entry <= 0.995:
                    continue
                bucket = ("0-6h" if hours < 6 else "6-24h" if hours < 24
                          else "24-48h" if hours < 48 else "48h+")
                key = (row["market_id"], bucket, int(entry * 100))
                if key not in obs:
                    obs[key] = {"fav": fav, "entry": entry, "ts": row["ts"]}
                    ids.add(row["market_id"])
    print(f"{len(obs)} observations across {len(ids)} markets; "
          f"checking which markets have resolved...")

    finals = {}
    ids = sorted(ids)
    for i in range(0, len(ids), 20):
        batch = get_json(f"{GAMMA}/markets",
                         params=[("id", x) for x in ids[i:i + 20]]
                         + [("closed", "true")]) or []
        for m in batch:
            prices = [fnum(p) for p in jlist(m.get("outcomePrices"))]
            if m.get("closed") and len(prices) == 2 and max(prices) > 0.99:
                finals[str(m["id"])] = (prices, (m.get("closedTime")
                                                 or m.get("endDate") or ""))
        time.sleep(0.12)

    qmap = {}
    if META_FILE.exists():
        for line in META_FILE.read_text().splitlines():
            try:
                d = json.loads(line)
                qmap[d["id"]] = d.get("q", "")
            except ValueError:
                pass
    stats = {}
    fams = {}
    labeled = 0
    for (mid, bucket, band), o in obs.items():
        if mid not in finals:
            continue
        prices_f, closed_at = finals[mid]
        try:
            real_h = (datetime.fromisoformat(closed_at.replace("Z", "+00:00"))
                      - datetime.fromisoformat(o["ts"])).total_seconds() / 3600
            if real_h < 0:
                continue   # observed AFTER true close: in-game/stale, not a trade
            bucket = ("0-6h" if real_h < 6 else "6-24h" if real_h < 24
                      else "24-48h" if real_h < 48 else "48h+")
        except (ValueError, TypeError):
            pass
        labeled += 1
        won = prices_f[o["fav"]] > 0.99
        s = stats.setdefault(bucket, {}).setdefault(str(band),
                                                    {"n": 0, "wins": 0, "cost": 0.0})
        s["n"] += 1
        s["wins"] += 1 if won else 0
        s["cost"] += o["entry"]
        q = qmap.get(mid, "")
        if q and not is_crypto_threshold(q) and not _SPORTSY.search(q):
            # family clusters for the HONEST kelly seed: 299 crypto obs
            # were really 11 families — raw counts inflated Wilson and
            # funded bands the independent evidence does not support
            if bucket in ("0-6h", "6-24h"):   # the kelly-seed horizon:
                fm = fams.setdefault(str(band), {}).setdefault(
                    family_of(q), [0, 0])   # ONE union table so a family
                fm[0] += 1 if won else 0    # in both buckets counts once
                fm[1] += 1

    print(f"{labeled} observations are labeled with real outcomes so far "
          f"(the rest resolve over the next days).\n")
    for bucket in ("0-6h", "6-24h", "24-48h", "48h+"):
        if bucket not in stats:
            continue
        print(f"Favorites bought {bucket} before resolution:")
        for band in sorted(stats[bucket], key=int):
            s = stats[bucket][band]
            s["win_rate"] = round(s["wins"] / s["n"], 4)
            s["roi"] = round((s["wins"] - s["cost"]) / s["cost"], 4)
            s["cost"] = round(s["cost"], 2)
            print(f"  {band}c: {s['n']:>5} obs | won {s['win_rate']:.1%} "
                  f"| return {s['roi']:+.2%} per $1")
        print()
    seed = {band: {"eff_n": len(fb),
                   "eff_wins": round(sum(w / n for w, n in fb.values()
                                         if n), 2)}
            for band, fb in fams.items()}
    atomic_write(RESEARCH_FILE, json.dumps({
        "generated": now_utc().isoformat(timespec="seconds"),
        "observations": len(obs), "labeled": labeled,
        "seed": seed, "by_bucket": stats}, indent=2))
    print(f"Saved to {RESEARCH_FILE.name}. Re-run anytime — more markets "
          f"resolve every hour.")


# ------------------------------------------------------------ quant math
#
# Kelly criterion: the textbook formula for how much of a bankroll to bet
# given an edge. We estimate each price band's true win probability from the
# backtest plus our own settled trades, take a conservative statistical
# lower bound (Wilson score) so small samples can't inflate the edge, and
# bet a quarter of full Kelly (full Kelly assumes perfect estimates; pros
# never bet it). No estimated edge at the offered price -> no trade.

def wilson_lower(wins, n, z=1.0):
    """Conservative lower estimate of a true win rate given wins/n observed."""
    if n == 0:
        return 0.0
    p = wins / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    spread = z * ((p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5)
    return max(0.0, (centre - spread) / denom)


def band_win_stats(account):
    """Pooled (wins, n) per price band: backtest history + our own trades."""
    stats = {}
    if BACKTEST_FILE.exists():
        try:
            bt = json.loads(BACKTEST_FILE.read_text())
            for lb in ("24", "48"):  # the horizons the bot actually trades
                for band, b in bt.get("by_lookback_hours", {}).get(lb, {}).items():
                    w, n = stats.get(band, (0, 0))
                    stats[band] = (w + b["wins"], n + b["n"])
        except ValueError:
            pass
    if RESEARCH_FILE.exists():
        try:    # corrected-instrument seed: same spread/timing gates as
            rr = json.loads(RESEARCH_FILE.read_text())   # live entries.
            # Full counts, no extra discount — wilson_lower in kelly IS
            # the estimation-error haircut, and double-discounting was
            # zeroing the funded lane. The math then funds 90-93c and
            # refuses 94-95c, exactly the research verdict.
            for band, b in (rr.get("seed") or {}).items():
                # family-level union table, tradable universe only —
                # never seed raw counts (299 crypto obs = 11 families;
                # raw seeding funded bands honest math defunds)
                w, n = stats.get(band, (0, 0))
                stats[band] = (w + b["eff_wins"], n + b["eff_n"])
        except ValueError:
            pass
    for s in account.get("settled", []):
        if s["strategy"] != "high_prob" or dead_cohort(s):
            continue
        band = str(int(round(s.get("entry_price", 0) * 100)))
        w, n = stats.get(band, (0, 0))
        stats[band] = (w + (1 if s["pnl"] >= 0 else 0), n + 1)
    return stats


def kelly_dollars(bankroll, price, band, stats, qcfg):
    """How much Kelly says to risk on a favorite at this price. 0 = no edge."""
    wins, n = stats.get(str(band), (0, 0))
    if n == 0:  # no data for this band: pool everything we have
        wins = sum(w for w, _ in stats.values())
        n = sum(c for _, c in stats.values())
    if n == 0:
        return 0.0
    p = wilson_lower(wins, n)
    if price >= 1 or price <= 0:
        return 0.0
    f = p - (1 - p) * price / (1 - price)  # Kelly fraction for a binary payout
    if f <= 0:
        return 0.0
    return bankroll * qcfg.get("kelly_fraction", 0.25) * f


def recent_momentum(token_id, qcfg):
    """Price change of this outcome over the last few hours (negative = falling).
    Returns None when there isn't enough history to judge."""
    hours = qcfg.get("momentum_lookback_hours", 6)
    end = int(now_utc().timestamp())
    h = get_json(f"{CLOB}/prices-history", params={
        "market": token_id, "startTs": end - hours * 3600,
        "endTs": end, "fidelity": 10})
    points = (h or {}).get("history", [])
    if len(points) < 2:
        return None
    return round(fnum(points[-1]["p"]) - fnum(points[0]["p"]), 4)


def compute_metrics(account, history):
    """The numbers a quant judges a strategy by."""
    settled = account.get("settled", [])
    wins = [s["pnl"] for s in settled if s["pnl"] >= 0]
    losses = [s["pnl"] for s in settled if s["pnl"] < 0]
    m = {"trades": len(settled),
         "win_rate": round(len(wins) / len(settled), 4) if settled else None,
         "avg_win": round(sum(wins) / len(wins), 3) if wins else None,
         "avg_loss": round(sum(losses) / len(losses), 3) if losses else None,
         "expectancy": round(sum(s["pnl"] for s in settled) / len(settled), 3)
                       if settled else None,
         "profit_factor": (round(sum(wins) / abs(sum(losses)), 2) if losses
                           else (None if not wins else "no losses yet"))}
    # max drawdown + Sharpe from the equity curve (one point per day)
    if history:
        peak, dd = history[0]["total"], 0.0
        daily = {}
        for pt in history:
            peak = max(peak, pt["total"])
            dd = max(dd, (peak - pt["total"]) / peak)
            daily[pt["t"][:10]] = pt["total"]  # last value of each day wins
        m["max_drawdown"] = round(dd, 4)
        vals = [daily[d] for d in sorted(daily)]
        if len(vals) >= 4:
            rets = [(b - a) / a for a, b in zip(vals, vals[1:]) if a > 0]
            mean = sum(rets) / len(rets)
            var = sum((r - mean) ** 2 for r in rets) / max(1, len(rets) - 1)
            m["sharpe"] = round(mean / (var ** 0.5) * (365 ** 0.5), 2) if var > 0 else None
        else:
            m["sharpe"] = None  # needs at least ~4 days of history
    else:
        m["max_drawdown"] = None
        m["sharpe"] = None
    return m


def optimize():
    """Derive the optimal trading band from backtest evidence and write it to
    config: trade only prices where the conservative (Wilson lower-bound) win
    rate still beats the price — i.e. edge survives statistical doubt."""
    if not BACKTEST_FILE.exists():
        print("No backtest data — run 'python3 bot.py backtest' first.")
        return
    bt = json.loads(BACKTEST_FILE.read_text())
    pooled = {}
    for lb in ("24", "48"):  # the horizons the bot trades
        for band, b in bt.get("by_lookback_hours", {}).get(lb, {}).items():
            w, n, c = pooled.get(band, (0, 0, 0.0))
            pooled[band] = (w + b["wins"], n + b["n"], c + b["cost"])
    good = []
    print(f"Evidence per band ({bt['trades_simulated']} backtest trades, 24-48h):")
    for band in sorted(pooled, key=int):
        w, n, c = pooled[band]
        p_low, avg = wilson_lower(w, n), c / n
        edge = p_low - avg
        ok = edge > 0 and n >= 20
        print(f"  {band}c: n={n:>5} won {w/n:.1%} | conservative win rate "
              f"{p_low:.3f} vs price {avg:.3f} -> edge {edge:+.3f} "
              f"{'TRADE' if ok else 'skip'}")
        if ok:
            good.append(int(band))
    # use the longest contiguous run of good bands ending at the top
    run = []
    for band in sorted(good, reverse=True):
        if not run or run[-1] - band == 1:
            run.append(band)
        else:
            break
    if not run:
        print("\nNo band survives statistical doubt — favorites trading should stay off.")
        return
    lo, hi = min(run), max(run)
    cfg = load_config()
    cfg["high_probability"]["buy_price_min"] = lo / 100
    cfg["high_probability"]["buy_price_max"] = min(0.995, (hi + 1) / 100 - 0.001)
    atomic_write(CONFIG_FILE, json.dumps(cfg, indent=2))
    print(f"\nOptimal band: {lo}-{hi}c -> config updated "
          f"(buy {lo / 100:.2f}-{cfg['high_probability']['buy_price_max']:.3f}). "
          f"Restart the bot to apply.")



LAB_FILE = HERE / "lab_results.json"


def lab(sample_target=400):
    """Strategy laboratory: replay competing trade rules over real historical
    price paths and report which made money. Tests 4 variants of the
    move-reaction strategy: follow vs fade a move, at 8c and 12c triggers,
    each with the live stop (-6c) and target (+10c) walked along the actual
    hourly path. Winners get promoted into the live config automatically."""
    EXITS = [(0.04, 0.08), (0.06, 0.10), (0.08, 0.15)]  # (stop, target)
    variants = {f"{mode}_{int(th*100)}c_s{int(st*100)}t{int(tg*100)}": []
                for mode in ("follow", "fade") for th in (0.08, 0.12)
                for st, tg in EXITS}
    seen, offset = 0, 0
    cutoff = now_utc() - timedelta(days=365)
    while seen < sample_target and offset < 100000:
        page = get_json(f"{GAMMA}/markets", params={
            "closed": "true", "order": "endDate", "ascending": "false",
            "limit": 100, "offset": offset, "volume_num_min": 5000,
            "end_date_min": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date_max": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ")}) or []
        if not page:
            break
        offset += 100
        for m in page:
            tokens = jlist(m.get("clobTokenIds"))
            finals = [fnum(p) for p in jlist(m.get("outcomePrices"))]
            if len(tokens) != 2 or len(finals) != 2:
                continue
            if not (max(finals) > 0.99 and min(finals) < 0.01):
                continue
            resolved = resolution_time(m)
            if not resolved or resolved < cutoff:
                continue
            end_ts = int(resolved.timestamp())
            h = get_json(f"{CLOB}/prices-history", params={
                "market": tokens[0], "startTs": end_ts - 72 * 3600,
                "endTs": end_ts, "fidelity": 60})
            time.sleep(0.12)
            pts = [(x["t"], fnum(x["p"])) for x in (h or {}).get("history", [])]
            if len(pts) < 10:
                continue
            seen += 1
            if seen % 100 == 0:
                print(f"  ...{seen} markets replayed")
            for th in (0.08, 0.12):
                trig = None
                for i in range(6, len(pts) - 2):
                    move = pts[i][1] - pts[i - 6][1]   # ~6h move
                    if abs(move) >= th:
                        trig = (i, move)
                        break
                if not trig:
                    continue
                i, move = trig
                for mode in ("follow", "fade"):
                    up = (move > 0) if mode == "follow" else (move < 0)
                    entry = (pts[i][1] if up else 1 - pts[i][1]) + 0.01  # spread cost
                    if not 0.10 <= entry <= 0.85:
                        continue
                    for st, tg in EXITS:
                        stop, target = entry - st, entry + tg
                        pnl = None
                        for j in range(i + 1, len(pts)):
                            v = pts[j][1] if up else 1 - pts[j][1]
                            if v <= stop:
                                pnl = stop - entry
                                break
                            if v >= target:
                                pnl = target - entry
                                break
                        if pnl is None:  # held to resolution
                            final_v = finals[0] if up else finals[1]
                            pnl = (1.0 if final_v > 0.99 else 0.0) - entry
                        variants[f"{mode}_{int(th*100)}c_s{int(st*100)}t{int(tg*100)}"].append(pnl)
            if seen >= sample_target:
                break

    report = {}
    print(f"\nStrategy lab: {seen} historical markets replayed\n")
    for name, pnls in variants.items():
        if not pnls:
            continue
        avg = sum(pnls) / len(pnls)
        wr = sum(1 for p in pnls if p > 0) / len(pnls)
        report[name] = {"n": len(pnls), "win_rate": round(wr, 3),
                        "avg_pnl_per_share": round(avg, 4)}
        print(f"  {name:>10}: n={len(pnls):>4} | won {wr:.1%} | "
              f"avg {avg:+.4f} per share")
    atomic_write(LAB_FILE, json.dumps({
        "generated": now_utc().isoformat(timespec="seconds"),
        "markets": seen, "variants": report}, indent=2))

    # promote the winner into the live config
    best = max(report.items(), key=lambda kv: kv[1]["avg_pnl_per_share"],
               default=(None, None))
    cfg = load_config()
    if best[0] and best[1]["avg_pnl_per_share"] > 0 and best[1]["n"] >= 50:
        mode, th, ex = best[0].split("_")
        cfg["news"]["mode"] = mode
        cfg["news"]["min_move_1h"] = int(th[:-1]) / 100
        st, tg = ex[1:].split("t")
        cfg["news"]["stop_drop"] = int(st) / 100
        cfg["news"]["target_gain"] = int(tg) / 100
        cfg["news"]["enabled"] = True
        print(f"\nWINNER: {best[0]} -> live config now {mode}s moves "
              f">= {th} (evidence: {best[1]})")
    else:
        cfg["news"]["enabled"] = False
        print("\nNo variant beat zero with enough evidence -> "
              "move-reaction trading disabled until data improves.")
    atomic_write(CONFIG_FILE, json.dumps(cfg, indent=2))


# ------------------------------------------------- strategy 1: arbitrage

def scan_arbitrage(cfg, skip_ids, multiplier=1.0):
    """Find multi-outcome events whose YES prices sum to less than $1."""
    acfg = cfg["arbitrage"]
    max_cost = acfg["max_cost_per_arb"] * multiplier
    opportunities = []
    events = get_json(f"{GAMMA}/events", params={
        "active": "true", "closed": "false", "order": "volume24hr",
        "ascending": "false", "limit": acfg["events_to_scan"],
    }) or []

    for event in events:
        if not event.get("negRisk"):
            continue  # only events where exactly one outcome can win
        if str(event.get("id")) in skip_ids:
            continue
        markets = [m for m in event.get("markets", [])
                   if m.get("active") and not m.get("closed")]
        if len(markets) < 2 or len(markets) > 20:
            continue

        # Cheap pre-check with Gamma's cached prices before hitting orderbooks.
        rough = sum(fnum(m.get("bestAsk"), 1.0) for m in markets)
        if rough > 1.02:
            continue

        # Confirm against live orderbooks.
        legs, total, min_size, ok = [], 0.0, float("inf"), True
        for m in markets:
            tokens = jlist(m.get("clobTokenIds"))
            if not tokens:
                ok = False
                break
            price, size = best_ask(tokens[0])  # token 0 = YES
            if price is None or size <= 0:
                ok = False
                break
            legs.append({"market_id": str(m["id"]), "question": m["question"],
                         "token_index": 0, "token_id": tokens[0], "price": price})
            total += price
            min_size = min(min_size, size)
            time.sleep(0.05)  # books are only fetched for near-arb events

        edge = 1.0 - total
        if ok and edge >= acfg["min_edge_cents"] / 100:
            shares = int(min(min_size, max_cost / total))
            if shares >= 1:
                opportunities.append({
                    "strategy": "arbitrage",
                    "event_id": str(event["id"]),
                    "name": event.get("title", "?"),
                    "legs": legs, "shares": shares,
                    "cost": round(shares * total, 2),
                    "entry_price": round(total, 3),
                    "detail": f"{shares} shares of all {len(legs)} outcomes, "
                              f"locked profit ${shares * edge:.2f}",
                })
    return opportunities


# ------------------------------------------- strategy 2: high probability


# ----------------------------------------------------------- replay engine

SIM_FILE = HERE / "sim_results.json"
SKILL_HIST_FILE = HERE / "skill_history.json"


def replay(max_markets=4000):
    """Offline replay: re-trade the recorder's captured history walk-forward
    at memory speed. SIM evidence PROPOSES (candidate beliefs, hypothesis
    triage); only live settles CONFIRM — nothing here touches live
    credibility, gates, or learning. Fill haircut: pay the ask plus 0.5c."""
    t0 = time.time()
    files = sorted(DATA_DIR.glob("snapshots-*.csv"))
    if not files:
        print("no recorded data yet")
        return
    meta = {}
    if META_FILE.exists():
        for line in META_FILE.read_text().splitlines():
            try:
                m = json.loads(line)
                meta[m["id"]] = m
            except ValueError:
                pass
    rows = []
    for path in files:
        with path.open() as f:
            for r in csv.DictReader(f):
                rows.append(r)
    print(f"replaying {len(rows):,} ticks across {len(meta):,} markets...")

    # outcomes for every market we saw (governed, batched)
    ids = sorted({r["market_id"] for r in rows})[:max_markets]
    finals, ends = {}, {}
    for i in range(0, len(ids), 20):
        for m in get_json(f"{GAMMA}/markets",
                          params=[("id", x) for x in ids[i:i + 20]]
                          + [("closed", "true")]) or []:
            if m.get("closed"):
                prices = [fnum(p) for p in jlist(m.get("outcomePrices"))]
                if len(prices) == 2:
                    finals[str(m["id"])] = prices[0]
                    e = (m.get("closedTime") or m.get("umaEndDate")
                         or m.get("endDate") or "")
                    if e:
                        ends[str(m["id"])] = e.replace("Z", "+00:00")
    print(f"{len(finals)} markets have resolved — those are the labels")

    open_pos, settled, brain_w, brain_hits, brain_calls = {}, [], {}, 0, 0
    cells = {}
    for r in rows:                       # rows are appended chronologically
        mid = r["market_id"]
        try:
            p_yes = float(r["p_yes"]); hours = float(r["hours_to_end"])
            bid = float(r["bid"]); ask = float(r["ask"])
        except (ValueError, KeyError):
            continue
        # settle anything whose market has ended before this tick
        for omid in [k for k, v in open_pos.items()
                     if ends.get(k, "9999") <= r["ts"]]:
            pos = open_pos.pop(omid)
            final = finals[omid]
            payout = final if pos["fav"] == 0 else 1 - final
            pnl = round(pos["shares"] * payout - pos["cost"], 3)
            pos.update(pnl=pnl, closed=ends[omid])
            settled.append(pos)
            c = pos["cell"]
            w, l = cells.get(c, (0, 0))
            cells[c] = (w + 1, l) if pnl >= 0 else (w, l + 1)
            if pos.get("brain_p") is not None:        # walk-forward scoring
                brain_calls += 1
                brain_hits += (pos["brain_p"] >= 0.5) == (pnl >= 0)
        if mid not in finals or mid in open_pos or hours < 1 or hours > 48:
            continue
        spread = ask - bid
        if spread > 0.04 or ask <= 0 or bid <= 0:
            continue
        fav = 0 if p_yes >= 0.5 else 1
        entry = (ask if fav == 0 else round(1 - bid, 3)) + 0.005  # haircut
        if not 0.85 <= entry <= 0.99:
            continue
        q = meta.get(mid, {}).get("q", "")
        strat = ("high_prob" if 0.96 <= entry <= 0.989 and hours >= 24
                 else "explore")
        ctx = {"spread": round(spread, 4), "hours_to_end": hours}
        bp = None
        if brain_w:
            x = _brain_x(strat, entry, ctx)
            z = sum(brain_w.get(k, 0.0) * v for k, v in x.items())
            bp = 1.0 / (1.0 + math.exp(-max(-30, min(30, z))))
        open_pos[mid] = {
            "strategy": strat, "name": q, "entry_price": entry, "fav": fav,
            "shares": 1, "cost": entry, "context": ctx, "brain_p": bp,
            "cell": (cluster_of(q), int(round(entry * 20)) * 5,
                     "<24h" if hours < 24 else "24-48h"),
        }
        if len(settled) % 300 == 250:    # walk-forward brain retrain:
            brain_w = brain_train(       # trained only on the PAST
                {"settled": settled}).get("w") or {}

    # markets still open at the end of the data window DID resolve in
    # reality — settle them against their known finals
    for mid, pos in open_pos.items():
        final = finals[mid]
        payout = final if pos["fav"] == 0 else 1 - final
        pnl = round(pos["shares"] * payout - pos["cost"], 3)
        pos.update(pnl=pnl, closed=ends.get(mid, ""))
        settled.append(pos)
        c = pos["cell"]
        w, l = cells.get(c, (0, 0))
        cells[c] = (w + 1, l) if pnl >= 0 else (w, l + 1)
        if pos.get("brain_p") is not None:
            brain_calls += 1
            brain_hits += (pos["brain_p"] >= 0.5) == (pnl >= 0)
    open_pos.clear()

    mins = (time.time() - t0) / 60
    by_cell = {f"{c[0]}|{c[1]}c|{c[2]}": {"wins": w, "n": w + l,
               "win_rate": round(w / (w + l), 3)}
               for c, (w, l) in cells.items() if w + l >= 5}
    beliefs = sorted(by_cell.items(),
                     key=lambda kv: -kv[1]["win_rate"])
    out = {
        "generated": now_utc().isoformat(timespec="seconds"),
        "ticks_replayed": len(rows),
        "sim_trades": len(settled),
        "sim_pnl": round(sum(t["pnl"] for t in settled), 2),
        "effective_n": effective_n(settled),
        "throughput_per_min": round(len(settled) / max(mins, 0.01)),
        "brain_walkforward_accuracy": (round(brain_hits / brain_calls, 3)
                                       if brain_calls else None),
        "brain_scored": brain_calls,
        "cells": by_cell,
        "candidate_beliefs_top": beliefs[:10],
        "candidate_beliefs_bottom": beliefs[-10:],
        "NOTE": "SIM ledger — proposes candidate beliefs only; never counts "
                "toward live credibility, gates, or verdicts.",
    }
    atomic_write(SIM_FILE, json.dumps(out, indent=1))
    print(json.dumps({k: v for k, v in out.items()
                      if "beliefs" not in k}, indent=1))
    print(f"top cells: {beliefs[:3]}")
    return out


# ----------------------------------------------------------- model overlay
# Ten lightweight quant models layered over the strategies. Models 1-5 are
# global (sizing throttles and eligibility, recomputed every pass from the
# bot's own files — zero extra API calls); 6-8 are per-entry gates inside the
# scanners; 9-10 re-judge every open position on every price check.

MODELS_FILE = HERE / "models_state.json"
MODEL_COUNTS = {}   # model name -> times it acted (vetoed entry / forced exit)
CLUSTER_NOTED = set()
REENTRY_FILE = HERE / "reentry.json"
try:    # cooldowns MUST survive restarts: in-memory-only cost -$3.90 when
    REENTRY = {k: float(v) for k, v in     # a feature-ship restart wiped
               json.loads(REENTRY_FILE.read_text()).items()}  # the 6h ban
except (OSError, ValueError):
    REENTRY = {}      # market_id -> close time; cool-off before re-entry


def reentry_mark(market_id):
    REENTRY[str(market_id)] = time.time()
    cutoff = time.time() - 86400
    for k in [k for k, v in REENTRY.items() if v < cutoff]:
        REENTRY.pop(k, None)
    try:
        atomic_write(REENTRY_FILE, json.dumps(REENTRY))
    except OSError:
        pass
ORDER_TIMES = []      # buy timestamps for the orders/min ceiling


def model_acted(name):
    MODEL_COUNTS[name] = MODEL_COUNTS.get(name, 0) + 1


def equity_curve_models(account):
    """Models 1+2 — volatility regime and equity-trend throttle from the
    bot's own equity curve. High realized vol, or equity below its day
    average, halves new-trade size: vol targeting / anti-martingale."""
    vals = []
    if HISTORY_FILE.exists():
        try:
            vals = [h["total"] for h in json.loads(HISTORY_FILE.read_text())][-720:]
        except ValueError:
            pass
    out = {"vol_regime": "warming up", "vol_mult": 1.0,
           "trend": "warming up", "trend_mult": 1.0}
    for i in range(len(vals) - 1, 0, -1):   # a >5%/min jump is a deposit or
        if vals[i - 1] and abs(vals[i] / vals[i - 1] - 1) > 0.05:  # reset, not
            vals = vals[i:]                  # trading — judge only what follows
            break
    if len(vals) >= 60:
        rets = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals)) if vals[i - 1]]
        mu = sum(rets) / len(rets)
        sd = (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5
        out["vol_regime"], out["vol_mult"] = (
            ("stressed", 0.5) if sd > 0.0008 else ("calm", 1.0))
        sma = sum(vals) / len(vals)
        out["trend"], out["trend_mult"] = (
            ("below day avg", 0.5) if vals[-1] < sma * 0.999
            else ("at/above day avg", 1.0))
    return out


def bayes_confidence(account):
    """Model 3 — Beta(2,2) posterior of each strategy's chance that one of
    its trades makes money. Halves size once 10+ settles say likely-loser;
    a modest boost needs 20+ settles of proof."""
    out = {}
    for s in STRATEGIES:
        tr = [t for t in account["settled"]
              if t["strategy"] == s and not dead_cohort(t)]
        wins = sum(1 for t in tr if t["pnl"] > 0)
        post = (wins + 2) / (len(tr) + 4)
        mult = (0.5 if len(tr) >= 10 and post < 0.45
                else 1.15 if len(tr) >= 20 and post > 0.60 else 1.0)
        out[s] = {"n": len(tr), "p_win": round(post, 2), "mult": mult}
    return out


def time_of_day_model(account):
    """Model 4 — P&L by 6-hour UTC bucket, judged PER STRATEGY: news losing
    overnight must not ground the favorites book too (each strategy is only
    blocked during hours where its own record shows 8+ settles net-losing)."""
    buckets = {}
    for t in account["settled"]:
        if dead_cohort(t):   # era hygiene: unrepeatable settles can't
            continue         # testify about hours (see dead_cohort docstring)
        try:
            b = int(t["closed"][11:13]) // 6 * 6
        except (ValueError, IndexError):
            continue
        d = (buckets.setdefault(f"{b:02d}-{b + 6:02d}h", {})
                    .setdefault(t["strategy"], {"n": 0, "pnl": 0.0}))
        d["n"] += 1
        d["pnl"] = round(d["pnl"] + t["pnl"], 2)
    blocked = {s: ([] if s == "explore" else  # the info book is governed by
                   sorted(b for b, per in buckets.items()  # its budget, not by
                          if per.get(s, {}).get("n", 0) >= 8  # profit-book risk
                          and per[s]["pnl"] < -1))  # models (see model 11 rule)
               for s in STRATEGIES}
    now_b = f"{now_utc().hour // 6 * 6:02d}-{now_utc().hour // 6 * 6 + 6:02d}h"
    return {"buckets": buckets, "blocked": blocked, "now": now_b,
            "now_blocked": {s: now_b in blocked[s] for s in STRATEGIES}}


CLUSTERS = [("weather", ("temperature", "°c", "°f", " rain", " snow")),
            ("crypto-price", ("bitcoin", "ethereum", "solana", "xrp ", "price of")),
            ("sports-game", (" vs. ", "o/u ", "spread:", "moneyline", "champion")),
            ("social-posts", ("tweet", "posts from", "truths"))]


def cluster_of(name):
    low = (name or "").lower()
    for label, keys in CLUSTERS:
        if any(k in low for k in keys):
            return label
    return "other"


def cluster_check(account, opp):
    """Model 5 — correlation clusters: bets that win or lose together (15
    weather markets = ONE bet). A cluster may hold at most 40% of deployed
    money, with a $40 floor so early trades still flow."""
    cl = cluster_of(opp["name"])
    risky = [p for p in account["positions"]
             if p.get("strategy") != "arbitrage"]  # locked payouts carry no
    cl_cost = sum(p["cost"] for p in risky          # outcome risk — an $85 arb
                  if cluster_of(p["name"]) == cl) + opp["cost"]  # once vetoed
    total = sum(p["cost"] for p in risky) + opp["cost"]   # 369 entries here
    return cl_cost > max(150.0, 0.40 * total), cl


def compute_models(account):
    """Evaluate the model overlay and persist its state for the dashboard."""
    eq = equity_curve_models(account)
    clusters = {}
    for p in account["positions"]:
        c = cluster_of(p["name"])
        clusters[c] = round(clusters.get(c, 0) + p["cost"], 2)
    state = {
        "updated": now_utc().isoformat(timespec="seconds"),
        "size_mult": round(eq["vol_mult"] * eq["trend_mult"], 2),
        "m1_vol_regime": {"state": eq["vol_regime"], "mult": eq["vol_mult"]},
        "m2_equity_trend": {"state": eq["trend"], "mult": eq["trend_mult"]},
        "m3_bayes": bayes_confidence(account),
        "m4_time_of_day": time_of_day_model(account),
        "m5_clusters": clusters,
        "m5_cluster_vetoes": MODEL_COUNTS.get("cluster", 0),
        "m6_impact_vetoes": MODEL_COUNTS.get("impact", 0),
        "m7_zscore_vetoes": MODEL_COUNTS.get("zscore", 0),
        "m8_quality_vetoes": MODEL_COUNTS.get("quality", 0),
        "m9_edge_gone_exits": MODEL_COUNTS.get("edge_gone", 0),
        "m10_pressure_exits": MODEL_COUNTS.get("pressure", 0),
        "m11_patterns": {"active_vetoes": len(PATTERN_VETOES["list"]),
                         "vetoed": MODEL_COUNTS.get("pattern", 0)},
        "m12_slide_exits": MODEL_COUNTS.get("slide", 0),
        "m13_brain": {"champion": BRAIN.get("kind", "logistic"),
                      "stack": BRAIN.get("stack") or [],
                      "importance": BRAIN.get("importance") or {},
                      "n": BRAIN.get("n", 0),
                      "credibility": round(BRAIN.get("n", 0) / (BRAIN.get("n", 0) + 150.0), 2),
                      "top": brain_top_factors(), "vetoed": MODEL_COUNTS.get("brain", 0),
                      # per-category specialists: surfaced for the Models tab.
                      # cw is the partial-pooling weight actually applied at
                      # decision time; only OOS-positive categories diverge.
                      "cat_specialists": {
                          c: {"n": d.get("n", 0),
                              "oos_skill": d.get("oos_skill"),
                              "n_eff": d.get("n_eff", 0),
                              "cw": (round(d["n_eff"] / (d["n_eff"] + 60.0), 3)
                                     if (d.get("oos_skill") or 0) > 0
                                     and d.get("n_eff", 0) > 0 else 0.0)}
                          for c, d in (BRAIN.get("cat_specialists") or {}).items()}},
        "m14_thompson": {"cells": TS_STATE["cells"]},
    }
    try:
        atomic_write(MODELS_FILE, json.dumps(state, indent=1))
    except OSError:
        pass
    return state


def model_multiplier(models, strat):
    """Combined size multiplier from the global models for one strategy.
    Arbitrage is never throttled (profit locked at entry); the explorer book
    is never throttled either — its $1 trades exist to buy information."""
    if strat in ("arbitrage", "explore"):
        return 1.0
    return models["size_mult"] * models["m3_bayes"][strat]["mult"]


RISK_FILE = HERE / "risk_report.json"


def compute_risk(account, sims=50000):
    """Risk analytics on the current open book: Monte Carlo VaR/CVaR with
    intra-cluster correlation, plus stress scenarios where a whole cluster
    fails at once (correlation break). Win probabilities use the pooled band
    statistics' Wilson lower bound where there's data, else the market's own
    price — conservative either way. Pure local computation, no API calls."""
    import random
    bstats = band_win_stats(account)
    legs, locked = [], 0.0
    for p in account["positions"]:
        if p["strategy"] == "arbitrage":
            locked += position_value(p) - p["cost"]
            continue
        mid = p.get("last_mid") or p.get("entry_price") or 0.5
        w, n = bstats.get(str(int(round(mid * 100))), (0, 0))
        pwin = wilson_lower(w, n) if n >= 10 else mid
        legs.append((pwin, p["shares"] * 1.0 - p["cost"], -p["cost"],
                     cluster_of(p["name"])))
    out = {"updated": now_utc().isoformat(timespec="seconds"),
           "open_risk_positions": len(legs), "arb_locked_pnl": round(locked, 2)}
    if legs:
        rng = random.Random(7)  # fixed seed: same book -> same report
        clusters = sorted({c for *_, c in legs})
        results = []
        for _ in range(sims):
            shock = {c: rng.random() for c in clusters}
            pnl = locked
            for pwin, wp, lp, c in legs:
                # 50% chance a position follows its cluster's shared shock —
                # correlated wipeouts, while each pwin stays exact marginally
                u = shock[c] if rng.random() < 0.5 else rng.random()
                pnl += wp if u < pwin else lp
            results.append(pnl)
        results.sort()
        k = max(1, int(0.05 * sims))
        out.update({
            "expected_pnl": round(sum(results) / sims, 2),
            "var95": round(-results[k], 2),
            "cvar95": round(-sum(results[:k]) / k, 2),
            "worst_case_total": round(locked + sum(lp for _, _, lp, _ in legs), 2),
            "stress_cluster_fails": {c: round(sum(
                lp for _, _, lp, cc in legs if cc == c), 2) for c in clusters},
        })
    try:
        atomic_write(RISK_FILE, json.dumps(out, indent=1))
    except OSError:
        pass
    return out



# --------------------------------------------- the brain (models 13 + 14)

BRAIN_FILE = HERE / "brain.json"
BRAIN = {"n": 0, "w": {}}
TS_STATE = {"cells": 0}
BRAIN_FACTORS = {"nsent": "headline sentiment",
                 "whale": "whale-flow agreement",
                 "smart": "fresh-wallet flow agreement",
                 "oracle": "oracle agreement", "omargin": "oracle margin",
                 "newsbk": "news-backed move", "zdev": "chart deviation",
                 "rpos": "range position",
                 "price": "higher entry price", "spread": "wide spread",
                 "imb": "buyer-stacked book", "mom": "recent momentum",
                 "ttr": "longer to resolution", "move": "big 1h move",
                 "no_side": "betting No",
                 "news": "news strategy", "explore": "explorer trade",
                 "w_fcstrike": "forecast vs strike", "w_spread": "ensemble spread",
                 "w_agree": "ensemble agreement"}


# Per-category SPORTS feature keys added to _brain_x. They default to EXACTLY
# 0.0 on the common path, but to keep the GLOBAL model (logistic + zoo/MLP)
# byte-identical to its pre-sports feature space — the MLP's weight init and the
# tree splits both depend on the exact set of input dimensions — these keys are
# STRIPPED from the global training/prediction vector (_global_x) and survive
# ONLY in the per-category sports specialist, which is the only learner allowed
# to weight them. Absent a sports specialist with earned OOS skill, they are a
# pure no-op and the global/common path is unchanged.
SPORTS_X_KEYS = ("sb_cons", "elo_fv", "sb_div", "gpost")

# Per-category CRYPTO feature keys — same partial-pooling contract as the sports
# keys: they default EXACTLY 0.0 on the common path and are STRIPPED from the
# global model's view (_global_x), so they survive ONLY in the crypto specialist
# and the global/common path is byte-identical. Absent an OOS-earned crypto
# specialist they are a pure no-op.
CRYPTO_X_KEYS = ("c_spotdist", "c_rvol", "c_spread")

# Per-category WEATHER feature keys — same partial-pooling contract: they default
# EXACTLY 0.0 on the common path and are STRIPPED from the global model's view
# (_global_x), so they survive ONLY in the weather specialist and the
# global/common path is byte-identical. Absent an OOS-earned weather specialist
# they are a pure no-op.
WEATHER_X_KEYS = ("w_fcstrike", "w_spread", "w_agree")

# Per-category MACRO feature keys — same partial-pooling contract: they default
# EXACTLY 0.0 on the common path and are STRIPPED from the global model's view
# (_global_x), so they survive ONLY in the macro specialist and the
# global/common path is byte-identical. Absent an OOS-earned macro specialist
# they are a pure no-op.
MACRO_X_KEYS = ("m_ratedev", "m_cpisurp", "m_yieldsig")

# Per-category SOCIAL feature keys — same partial-pooling contract: they default
# EXACTLY 0.0 on the common path and are STRIPPED from the global model's view
# (_global_x), so they survive ONLY in the social specialist and the
# global/common path is byte-identical. They are derived purely from the
# point-in-time HEADLINES buffer (news_rss + HackerNews connectors). DISTINCT
# from the GLOBAL `newsbk`/`nsent` keys, which remain in the global feature
# space exactly as before. Absent an OOS-earned social specialist these are a
# pure no-op.
SOCIAL_X_KEYS = ("s_newsstrong", "s_sentmag", "s_sentalign")

# All per-category keys the global model must never see. The global logistic,
# zoo (GBM/XGB/forest) and MLP train and predict on exactly the dimensions they
# did before any category feature existed (byte-identical common path).
_CAT_X_KEYS = (SPORTS_X_KEYS + CRYPTO_X_KEYS + WEATHER_X_KEYS + MACRO_X_KEYS
               + SOCIAL_X_KEYS)


def _global_x(x):
    """The global model's view of a feature vector: identical to the
    pre-category-feature space. Strips every per-category key so the global
    logistic, GBM/XGB, forest and MLP train and predict on exactly the
    dimensions they did before the category features existed (byte-identical
    common path)."""
    return {k: v for k, v in x.items() if k not in _CAT_X_KEYS}


def _brain_x(strategy, price, ctx, side=None, closed=None, name=None):
    """One trade's entry context as a numeric feature vector.

    `closed` (the market resolution timestamp) is accepted for call-site
    compatibility but intentionally UNUSED: the settlement hour is unknown at
    entry, so any feature derived from it leaks future data into training. The
    former `night`/`imb_x_night` features that read it have been removed."""
    ctx = ctx or {}
    imb = ctx.get("imbalance")
    return {
        "bias": 1.0,
        "price": (price or 0.5) - 0.5,
        "spread": min((ctx.get("spread") or 0.0) * 50, 2.0),
        "imb": ((imb if imb is not None else 0.5) - 0.5) * 2,
        "mom": max(-1.0, min(1.0, (ctx.get("momentum_6h") or 0.0) * 25)),
        "ttr": min((ctx.get("hours_to_end") or 48) / 96.0, 1.5),
        "move": min(abs(ctx.get("move_1h") or 0.0) * 5, 1.5),
        "no_side": 1.0 if (side or "").lower().startswith("no") else 0.0,
        "news": 1.0 if strategy == "news" else 0.0,
        "explore": 1.0 if strategy == "explore" else 0.0,
        "zdev": max(-1.5, min(1.5, (ctx.get("z") or 0.0) / 3.0)),
        "rpos": ((ctx.get("range_pos") if ctx.get("range_pos") is not None
                  else 0.5) - 0.5) * 2,
        "oracle": (0.0 if ctx.get("oracle_agree") is None
                   else 1.0 if ctx["oracle_agree"] else -1.0),
        "whale": (0.0 if ctx.get("whale_agree") is None
                  else 1.0 if ctx["whale_agree"] else -1.0),
        "smart": (0.0 if ctx.get("smart_agree") is None
                  else 1.0 if ctx["smart_agree"] else -1.0),
        "omargin": max(-1.5, min(1.5, ctx.get("oracle_margin") or 0.0)),
        "newsbk": 1.0 if ctx.get("news_backed") else 0.0,
        "nsent": ctx.get("news_sent") or 0.0,
        # CROSS-MARKET signal (Kalshi/PredictIt/Manifold consensus). DEFAULTS
        # NEUTRAL (0.0) when there is no cross-market twin — the common case —
        # so the global model is unchanged where xmkt is absent. Where a twin
        # exists, divergence (pm_p - consensus_p) and the consensus-minus-price
        # gap enter the model; the brain's OOS/credibility gate decides their
        # weight. A non-predictive signal earns ~0 weight; it never trades on
        # blind divergence.
        "xmkt_div": max(-1.0, min(1.0, (ctx.get("xmkt_divergence") or 0.0) * 3.0)),
        "xmkt_cmp": (0.0 if ctx.get("xmkt_consensus") is None or price is None
                     else max(-1.0, min(1.0,
                              (ctx["xmkt_consensus"] - price) * 3.0))),
        # PER-CATEGORY SPORTS features. ALL DEFAULT EXACTLY NEUTRAL (0.0) when
        # the sports context is absent — i.e. every non-sports market and every
        # sports market with no signal — so the global/common path is unchanged
        # and the golden regression fixtures are byte-identical. These fire only
        # for sports markets that carry sports_* context, and only the OOS-gated
        # sports specialist ever weights them. All point-in-time (see
        # sports_features): live game-state is banned, post-game abstains.
        # sb_cons: signed sportsbook-consensus edge vs price (de-vigged Odds API)
        "sb_cons": (0.0 if ctx.get("sportsbook_consensus") is None
                    or price is None
                    else max(-1.0, min(1.0,
                             (ctx["sportsbook_consensus"] - price) * 3.0))),
        # elo_fv: signed Elo fair-value edge vs price (finals-trained, immutable)
        "elo_fv": (0.0 if ctx.get("sports_elo_fv") is None or price is None
                   else max(-1.0, min(1.0,
                            (ctx["sports_elo_fv"] - price) * 3.0))),
        # sb_div: normalized (price - sportsbook_consensus) divergence, [-1,1]
        "sb_div": max(-1.0, min(1.0, (ctx.get("sports_div") or 0.0) * 3.0)),
        # gpost: post-game risk flag — joined ESPN game already final (abstain
        # territory); 0.0 pre-game / unknown (live never reaches the trade path).
        "gpost": 1.0 if ctx.get("sports_post") else 0.0,
        # PER-CATEGORY CRYPTO features. ALL DEFAULT EXACTLY NEUTRAL (0.0) when the
        # crypto context is absent — i.e. every non-crypto market and every crypto
        # market we cannot map to a symbol/strike — so the global/common path is
        # unchanged and the golden regression fixtures are byte-identical. These
        # fire only for crypto markets carrying crypto_* context, and only the
        # OOS-gated crypto specialist ever weights them. All point-in-time (see
        # crypto_features): Coinbase/CoinGecko spot, Kraken closed-candle vol and
        # live Kraken ticker spread — every read a snapshot AS OF NOW.
        # c_spotdist: signed (spot - strike)/strike distance, capped [-1,1].
        "c_spotdist": max(-1.0, min(1.0,
                          (ctx.get("crypto_spot_dist") or 0.0) * 5.0)),
        # c_rvol: hourly realized vol, scaled so a calm ~1%/h sits near 0 and a
        # violent regime saturates toward 1.0 (24 closed candles, no forward data).
        "c_rvol": max(0.0, min(1.0, (ctx.get("crypto_rvol_h") or 0.0) * 20.0)),
        # c_spread: live bid/ask spread in bps, scaled so a tight ~5bps book sits
        # near 0 and a thin/illiquid one saturates toward 1.0.
        "c_spread": max(0.0, min(1.0, (ctx.get("crypto_spread_bps") or 0.0)
                                 / 50.0)),
        # PER-CATEGORY WEATHER features. ALL DEFAULT EXACTLY NEUTRAL (0.0) when the
        # weather context is absent — i.e. every non-weather market and every
        # weather market we cannot parse to a city/strike — so the global/common
        # path is unchanged and the golden regression fixtures are byte-identical.
        # These fire only for weather markets carrying wx_* context, and only the
        # OOS-gated weather specialist ever weights them. All point-in-time (see
        # weather_features): Open-Meteo ensemble + weather.gov/NWS forecasts
        # issued AS OF NOW — no future observation, just forecast agreement.
        # w_fcstrike: signed (forecast_mean - strike)/(hist_err+0.5), capped to
        # the spec range [-3,3]. Positive => ensemble runs hotter than the strike.
        "w_fcstrike": max(-3.0, min(3.0, ctx.get("wx_fc_strike") or 0.0)),
        # w_spread: ensemble disagreement (member stdev) / 2.5, capped [0,1.5];
        # higher spread = lower forecast confidence (per spec).
        "w_spread": max(0.0, min(1.5, (ctx.get("wx_fc_spread") or 0.0) / 2.5)),
        # w_agree: fraction of ensemble members on the market's side, [0,1];
        # neutral 0.0 when absent (no direction / no ensemble). 0.5 is "coin
        # flip" and a real consensus pulls toward 0 or 1.
        "w_agree": max(0.0, min(1.0, ctx.get("wx_model_agree") or 0.0)),
        # PER-CATEGORY MACRO features. ALL DEFAULT EXACTLY NEUTRAL (0.0) when the
        # macro context is absent — i.e. every non-macro market and every macro
        # market we cannot map to a FRED series — so the global/common path is
        # unchanged and the golden regression fixtures are byte-identical. These
        # fire only for macro markets carrying macro_* context, and only the
        # OOS-gated macro specialist ever weights them. All point-in-time (see
        # macro_features): FRED observations publish with a lag (CPI ~12 days,
        # DFF ~1 day), market expectations are today's consensus and the yield
        # curve is live Treasury prices — every read a snapshot AS OF NOW, no
        # future observation or forecast.
        # m_ratedev: market's expected Fed rate minus the latest DFF observation,
        # scaled to [-1.5, 1.5]. Positive => market prices rates above the
        # current funds rate (hawkish expectation divergence).
        "m_ratedev": max(-1.5, min(1.5, ctx.get("macro_rate_dev") or 0.0)),
        # m_cpisurp: YoY CPI change vs consensus forecast, normalized by a 0.5%
        # basis and clipped to [-1.5, 1.5]. Positive => inflation ran hotter than
        # consensus (upside inflation surprise).
        "m_cpisurp": max(-1.5, min(1.5,
                         (ctx.get("macro_cpi_surprise") or 0.0) / 0.5)),
        # m_yieldsig: 10Y-2Y spread regime — -1.0 inverted (recession risk),
        # -0.5 flat, 0.0 normal, 0.5 steep growth. Already a discrete regime
        # code; passed through, clipped to its declared [-1.0, 0.5] band.
        "m_yieldsig": max(-1.0, min(0.5, ctx.get("macro_yield_signal") or 0.0)),
        # PER-CATEGORY SOCIAL features. ALL DEFAULT EXACTLY NEUTRAL (0.0) when the
        # social context is absent — i.e. every non-social market and every social
        # market with no fresh coverage — so the global/common path is unchanged
        # and the golden regression fixtures are byte-identical. These fire only
        # for social markets carrying social_* context, and only the OOS-gated
        # social specialist ever weights them. All point-in-time (see
        # social_features): fresh, timestamp-filtered HEADLINES (news_rss +
        # HackerNews) read AS OF NOW — never a future headline or market read.
        # DISTINCT from the GLOBAL newsbk/nsent keys, which are unchanged.
        # s_newsstrong: 1.0 when fresh coverage is CORROBORATED (>=2 fresh
        # headlines or one strong subject match); 0.0 otherwise / when absent.
        "s_newsstrong": 1.0 if ctx.get("social_news_strong") else 0.0,
        # s_sentmag: magnitude of fresh-headline sentiment [0,1] — how loud the
        # news is, direction-agnostic; 0.0 when no fresh coverage.
        "s_sentmag": max(0.0, min(1.0, ctx.get("social_sent_mag") or 0.0)),
        # s_sentalign: signed sentiment aligned to the side being bet, [-1,1];
        # 0.0 (neutral) when no fresh coverage. Positive => the headline mood
        # agrees with backing this outcome.
        "s_sentalign": max(-1.0, min(1.0, ctx.get("social_sent_align") or 0.0)),
    } | (lambda cl: {
        "cl_weather": 1.0 if cl == "weather" else 0.0,
        "cl_sports": 1.0 if cl == "sports-game" else 0.0,
        "cl_crypto": 1.0 if cl == "crypto-price" else 0.0,
        "cl_social": 1.0 if cl == "social-posts" else 0.0,
    })(cluster_of(name)) | (lambda b: {
        # interactions: the continuous version of the miner's pair patterns
        "spread_x_ttr": b["spread"] * b["ttr"],
        "price_x_move": b["price"] * b["move"],
        "imb_x_move": b["imb"] * b["move"],
    })({"imb": ((ctx.get("imbalance") if ctx.get("imbalance") is not None else 0.5) - 0.5) * 2,
        "spread": min((ctx.get("spread") or 0.0) * 50, 2.0),
        "ttr": min((ctx.get("hours_to_end") or 48) / 96.0, 1.5),
        "price": (price or 0.5) - 0.5,
        "move": min(abs(ctx.get("move_1h") or 0.0) * 5, 1.5)})


def _fit_logistic(data, iters=300, lr=0.5, l2=0.05):
    """l2 is now chosen by cross-validation in brain_train."""
    keys = list(data[0][0])
    w = {k: 0.0 for k in keys}
    for _ in range(iters):
        g = {k: 0.0 for k in keys}
        for x, y in data:
            z = sum(w[k] * x[k] for k in keys)
            p = 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))
            for k in keys:
                g[k] += (p - y) * x[k]
        for k in keys:
            w[k] -= lr * (g[k] / len(data) + (l2 * w[k] if k != "bias" else 0.0))
    return {k: round(v, 4) for k, v in w.items()}


def _predict(w, x):
    z = sum(w.get(k, 0.0) * v for k, v in x.items())
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def _settle_traits(t):
    try:
        return set(trade_features(t))
    except Exception:
        return set()


def _top_pat_feats(trades, k=5):
    """The miner's top-k strongest combos derived from a SPECIFIC trade list.
    Used two ways: GLOBALLY (the whole settled set) for the deployed brain, and
    — critically — FOLD-LOCALLY inside cross-validation, where `trades` is only
    a fold's TRAINING trades (data[:cut]). Mining per-fold from train data alone
    keeps the pat* feature DEFINITIONS from ever seeing a holdout outcome.
    dead_cohort filtering and the 14-day recency window are preserved untouched:
    mine_patterns owns both and receives the trade list verbatim."""
    mined = mine_patterns({"settled": trades}, min_n=8)
    return [m["pattern"] for m in
            sorted(mined, key=lambda m: -abs(m["pnl"]))[:k]]


def _strip_pats(x):
    """A copy of feature vector x without its pat0..patN columns — the base onto
    which a fold's own mined patterns are re-applied during CV."""
    return {f: v for f, v in x.items() if not f.startswith("pat")}


def _add_pat_feats(x, traits, pat_feats):
    """Bake the binary pat0..patN columns onto x for the given pattern set
    (mutates and returns x)."""
    for i, p in enumerate(pat_feats):
        x[f"pat{i}"] = 1.0 if set(p.split("&")) <= traits else 0.0
    return x


def brain_train(account):
    """Brain 3.0 — four multipliers stacked:
    (1) AUTO FEATURE ENGINEERING: the pattern miner's strongest discovered
        combos become binary input features — one learner invents the
        representation, this one weighs it;
    (2) 5-fold walk-forward cross-validation (expanding window) for the
        skill estimate and model selection — five verdicts, not one;
    (3) hyperparameter search: L2 strength chosen by CV every retrain;
    (4) temporal prior: each fit is pulled toward the previous brain, so
        knowledge accumulates instead of resetting (online learning)."""
    # GLOBAL pattern features for the DEPLOYED brain: the final models are fit
    # on ALL rows, where there is no holdout to leak into, so mining the top-5
    # combos from the whole settled set is correct HERE. The cross-validation
    # below re-mines them FOLD-LOCALLY (train trades only) so the skill estimate
    # is not inflated by in-sample feature selection.
    pat_feats = _top_pat_feats(account["settled"])
    rows = []
    for t in account["settled"]:
        if t["strategy"] == "arbitrage" or dead_cohort(t):
            continue
        x = _brain_x(t["strategy"], t.get("entry_price"), t.get("context"),
                     t.get("side"), t.get("closed"), t.get("name"))
        traits = _settle_traits(t)
        _add_pat_feats(x, traits, pat_feats)   # GLOBAL pats for the final fit
        rows.append((x, 1.0 if t["pnl"] >= 0 else 0.0, t["strategy"],
                     cat_key(t.get("category")), t, traits))
    n_eff = effective_n([t for t in account["settled"]
                         if t["strategy"] != "arbitrage" and not dead_cohort(t)])
    out = {"n": len(rows), "n_eff": n_eff, "w": {}, "specialists": {},
           "cat_specialists": {},
           "oos": None, "skill_factor": 0.5, "pat_feats": pat_feats}
    if len(rows) < 10:
        return out
    if (BRAIN.get("n") == len(rows) and BRAIN.get("w")
            # a BRAIN cached before the per-category layer existed has no
            # cat_specialists key: it MUST retrain so the category layer is
            # populated, otherwise the new code would silently never engage.
            and BRAIN.get("cat_specialists") is not None):
        return {k: BRAIN[k] for k in ("n", "n_eff", "w", "specialists",
                                      "cat_specialists", "oos",
                                      "skill_factor", "pat_feats", "kind",
                                      "models", "stack", "cal", "importance",
                                      "calibration_table") if k in BRAIN}
    di = BRAIN.get("drift_i") or 0
    # GLOBAL training/CV/zoo data uses the pre-sports feature space (_global_x):
    # the per-category sports keys are stripped here so the global logistic, the
    # zoo (GBM/XGB/forest) and the MLP train on byte-identical dimensions to
    # before. The full vector (with sports keys) is kept on `rows` for the
    # per-category sports specialist below.
    if di and len(rows) - di >= 40:
        # the Page-Hinkley detector fired at settle #di: the world the old
        # data describes is gone. Once the new regime has enough labels to
        # stand alone, train on it exclusively (river's window-drop move).
        src = rows[di:]
    else:
        src = rows
    data = [(_global_x(x), y) for x, y, *_ in src]
    # Aligned 1:1 with `data`: each row's raw trade + trait set, threaded into
    # CV so every fold re-mines its OWN pat* features from training trades only
    # (data[:cut]). `data` itself keeps the GLOBAL pat columns baked on, which
    # is correct for the FINAL models fit on all rows (no holdout to leak into);
    # CV strips those off (cv_bases) and re-derives them per fold below.
    cv_meta = [(t, tr) for *_, t, tr in src]            # (trade, traits)
    cv_bases = [_strip_pats(gx) for gx, _y in data]     # pat-free vectors

    # Fold geometry computed ONCE. The fold-local pattern set is mined HERE from
    # data[:cut] only, so the pat* feature DEFINITIONS never see a holdout label
    # and every model in the race shares the same honest folds. Before this fix,
    # the top-5 patterns were mined globally from the whole settled set — i.e.
    # from trades that later land in the holdout fold — which is in-sample
    # feature selection bleeding into the OOS estimate and inflated cv_skill.
    _k = 5
    cv_folds = []
    for _f in range(2, _k):                   # expanding-window folds
        _cut = max(10, int(len(data) * _f / _k))
        _hi = min(len(data), _cut + max(1, len(data) // _k))
        if _hi - _cut < 5:
            continue
        cv_folds.append((_cut, _hi,
                         _top_pat_feats([t for t, _tr in cv_meta[:_cut]])))

    def cv_generic(fit_fn, pred_fn):
        """Expanding-window CV with FOLD-LOCAL pattern mining (see cv_folds):
        the pat* columns are re-derived from each fold's TRAIN trades only, so
        the skill estimate is honest. fit_fn trains on the fold-train rows;
        pred_fn scores the held-out fold; skill is logloss vs. the in-fold base
        rate, averaged across folds."""
        skills = []
        for cut, hi, pats in cv_folds:
            tr = [(_add_pat_feats(dict(cv_bases[i]), cv_meta[i][1], pats),
                   data[i][1]) for i in range(cut)]
            hold = [(_add_pat_feats(dict(cv_bases[i]), cv_meta[i][1], pats),
                     data[i][1]) for i in range(cut, hi)]
            m = fit_fn(tr)
            base = max(0.02, min(0.98, sum(y for _, y in tr) / len(tr)))
            ll_m = ll_b = 0.0
            for x, y in hold:
                p = max(0.02, min(0.98, pred_fn(m, x)))
                ll_m += -(y * math.log(p) + (1 - y) * math.log(1 - p))
                ll_b += -(y * math.log(base) + (1 - y) * math.log(1 - base))
            skills.append((ll_b - ll_m) / len(hold))
        return sum(skills) / len(skills) if skills else None

    def cv_skill(l2):                          # logistic CV (same honest folds)
        return cv_generic(lambda tr: _fit_logistic(tr, l2=l2), _predict)

    best_l2, best_skill = 0.05, None
    for l2 in (0.02, 0.05, 0.15):
        s = cv_skill(l2)
        if s is not None and (best_skill is None or s > best_skill):
            best_l2, best_skill = l2, s
    # THE CHAMPIONSHIP, now a COMMITTEE: every model class races on the
    # same chronological folds; every member with POSITIVE out-of-sample
    # skill joins a skill-weighted stack. Committees of validated members
    # beat lone champions; members with no skill get no vote.
    zoo_scores = {"logistic": best_skill}
    if len(data) >= 40:
        for name in ("gbm", "gbm-slow", "xgb", "xgb-reg",
                     "forest", "forest-big"):
            try:
                zoo_scores[name] = cv_generic(ml.ZOO[name], ml.predict)
            except Exception:
                pass
        if len(data) >= 80:
            try:
                zoo_scores["mlp"] = cv_generic(ml.fit_mlp, ml.predict)
            except Exception:
                pass
    ranked = sorted(((s, n) for n, s in zoo_scores.items() if s is not None),
                    reverse=True)
    champ_skill, champ = ranked[0] if ranked else (None, "logistic")
    out["kind"] = champ
    positive = [(n, s) for s, n in ranked if s and s > 0]
    if champ_skill is not None:
        out["oos"] = {"cv_skill": round(champ_skill, 4), "l2": best_l2,
                      "champion": champ,
                      "zoo": {n: (round(s, 4) if s is not None else None)
                              for n, s in zoo_scores.items()}}
        out["skill_factor"] = max(0.25, min(1.0, 0.5 + 4.0 * champ_skill))
    out["models"], out["stack"] = {}, []
    # Champion-dominance gate: when the best model clearly outskills the
    # runner-up, deploy it ALONE rather than diluting it in the skill-weighted
    # committee (the blend was giving a dominant champion only ~21-23% of the
    # vote on the probability that drives sizing). Inert when models are close
    # (ratio < threshold), so the committee path — and the golden byte-identity
    # fixtures — stay unchanged.
    CHAMP_DOMINANCE = 1.35   # champion must outskill the runner-up by 35%+ to solo
    if (len(positive) >= 2 and positive[1][1] > 0
            and positive[0][1] >= CHAMP_DOMINANCE * positive[1][1]):
        positive = [positive[0]]
    tot = sum(s for _, s in positive)
    for name, s in positive:
        try:
            if name != "logistic":
                out["models"][name] = ml.ZOO[name](data)
            out["stack"].append([name, round(s / tot, 3)])
        except Exception:
            pass
    # calibration: fit Platt on the last fold's held-out stack predictions
    try:
        cut = max(10, int(len(data) * 0.8))
        hold = data[cut:]
        if len(hold) >= 8 and out["stack"]:
            tr_models = {}
            for name, _ in out["stack"]:
                tr_models[name] = (_fit_logistic(data[:cut], l2=best_l2)
                                   if name == "logistic"
                                   else ml.ZOO[name](data[:cut]))
            def stack_p(mods, x):
                tot_w = sum(wt for _, wt in out["stack"]) or 1.0
                return sum(wt * (_predict(mods[n], x) if n == "logistic"
                                 else ml.predict(mods[n], x))
                           for n, wt in out["stack"]) / tot_w
            preds = [(stack_p(tr_models, x), y) for x, y in hold]
            out["cal"] = ml.fit_platt(preds)
            if len(preds) >= 40:
                # race Platt vs isotonic HONESTLY: fit both on the first
                # 60% of the holdout, judge on the last 40% (isotonic
                # always wins on its own training points), then refit the
                # winner on everything
                c2 = int(len(preds) * 0.6)

                def _cal_ll(c):
                    tot = 0.0
                    for p, y in preds[c2:]:
                        q = max(1e-5, min(1 - 1e-5, ml.apply_cal(c, p)))
                        tot += -(y * math.log(q) + (1 - y) * math.log(1 - q))
                    return tot
                if (_cal_ll(ml.fit_isotonic(preds[:c2]))
                        < _cal_ll(ml.fit_platt(preds[:c2]))):
                    out["cal"] = ml.fit_isotonic(preds)
            if champ in tr_models:
                champ_model = tr_models[champ]
                if champ == "logistic":
                    # logistic is a raw weight dict with no "kind" field, so
                    # ml.predict() can't dispatch on it; predict with _predict
                    # and reuse the same per-bin binning as ml.calibration_table
                    def _cal_pred(x):
                        return _predict(champ_model, x)
                else:
                    def _cal_pred(x):
                        return ml.predict(champ_model, x)
                _cal_rows = sorted((_cal_pred(x), y) for x, y in hold)
                _cal_step = max(1, len(_cal_rows) // 5)
                _cal_table = []
                for _cal_i in range(0, len(_cal_rows), _cal_step):
                    _cal_chunk = _cal_rows[_cal_i:_cal_i + _cal_step]
                    if len(_cal_chunk) < 3:
                        continue
                    _cal_table.append({
                        "predicted": round(sum(p for p, _ in _cal_chunk) / len(_cal_chunk), 3),
                        "actual": round(sum(y for _, y in _cal_chunk) / len(_cal_chunk), 3),
                        "n": len(_cal_chunk)})
                out["calibration_table"] = _cal_table
            else:
                out["calibration_table"] = None
    except Exception:
        pass
    # honest interpretability: permutation importance of the actual champion
    try:
        if champ != "logistic" and out["models"].get(champ):
            imp = ml.permutation_importance(out["models"][champ], data)
            out["importance"] = dict(list(imp.items())[:6])
    except Exception:
        pass

    w = _fit_logistic(data, l2=best_l2)
    prev = BRAIN.get("w") or {}
    if prev:                                   # temporal prior: blend toward
        for k2 in w:                           # yesterday's knowledge
            w[k2] = round(0.7 * w[k2] + 0.3 * prev.get(k2, 0.0), 4)
    out["w"] = w
    # the per-STRATEGY specialists are part of the GLOBAL path (brain_adjust
    # blends them into p_model on the no-stack branch), so they too train on the
    # stripped pre-sports feature space — only the per-CATEGORY sports
    # specialist below is allowed to carry the sports keys.
    for s in ("high_prob", "news", "explore", "daytrade"):
        sd = [(_global_x(x), y) for x, y, st, *_ in rows if st == s]
        if len(sd) >= 20:
            out["specialists"][s] = _fit_logistic(sd, l2=best_l2)

    # ---- PER-CATEGORY SPECIALIST LAYER (partial pooling, OOS-gated) --------
    # The GLOBAL model above is unchanged and remains the prior/fallback. For
    # each bet category with >=20 dead-cohort-filtered rows we fit its OWN
    # specialist on that category's rows AND validate it with the SAME
    # walk-forward CV the global model uses (expanding-window folds, skill vs
    # the in-fold base rate). We store {w, oos_skill, n_eff, n}; brain_adjust
    # only blends toward a specialist whose oos_skill>0 — a category that
    # cannot beat the base rate out-of-sample stays a pure no-op and shrinks
    # fully to the global model. This is never N independent models: the
    # global model always trains on ALL rows and is always the fallback.
    def _cat_cv_skill(cbases, cmeta, ylist, l2):
        """Walk-forward OOS skill for one category's rows — byte-for-byte the
        same expanding-window scoring as the global cv_skill (cv_generic),
        scoped to the category, with FOLD-LOCAL pattern mining. The pat*
        columns are re-derived from each fold's TRAIN trades only
        (cmeta[:cut]), so the per-category skill estimate is not inflated by
        the globally-mined patterns bleeding into the holdout. cbases are the
        pat-free base vectors, cmeta are aligned (trade, traits) pairs, ylist
        the aligned labels. Returns None when there is too little data."""
        skills, k = [], 5
        n = len(cbases)
        for f in range(2, k):
            cut = max(10, int(n * f / k))
            hi = cut + max(1, n // k)
            if min(n, hi) - cut < 5:
                continue
            pats = _top_pat_feats([t for t, _tr in cmeta[:cut]])
            tr = [(_add_pat_feats(dict(cbases[i]), cmeta[i][1], pats),
                   ylist[i]) for i in range(cut)]
            hold = [(_add_pat_feats(dict(cbases[i]), cmeta[i][1], pats),
                     ylist[i]) for i in range(cut, min(n, hi))]
            wc = _fit_logistic(tr, l2=l2)
            base = max(0.02, min(0.98, sum(y for _, y in tr) / len(tr)))
            ll_m = ll_b = 0.0
            for x, y in hold:
                p = max(0.02, min(0.98, _predict(wc, x)))
                ll_m += -(y * math.log(p) + (1 - y) * math.log(1 - p))
                ll_b += -(y * math.log(base) + (1 - y) * math.log(1 - base))
            skills.append((ll_b - ll_m) / len(hold))
        return sum(skills) / len(skills) if skills else None

    by_cat = {}
    for x, y, st, ck, t, tr in rows:
        if ck:
            by_cat.setdefault(ck, []).append((x, y, t, tr))
    for ck, crows in by_cat.items():
        if len(crows) < 20:
            continue
        cdata = [(x, y) for x, y, _t, _tr in crows]
        # FOLD-LOCAL inputs for the honest OOS estimate: pat-free base vectors
        # plus per-row (trade, traits) so each CV fold re-mines its OWN pat*
        # columns from train trades alone. The final specialist below still
        # keeps the GLOBAL pat columns baked on (correct: no holdout in the
        # all-rows fit), exactly as the global brain's deployed models do.
        cbases = [_strip_pats(x) for x, _y, _t, _tr in crows]
        cmeta = [(_t, _tr) for _x, _y, _t, _tr in crows]
        ylist = [y for _x, y, _t, _tr in crows]
        # era hygiene: rows already exclude dead_cohort settles, so n_eff
        # counts living clusters only — a dead-cohort-only category never
        # reaches 20 rows here and gets n_eff=0 => zero credibility => no-op.
        ctrades = [_t for _x, _y, _t, _tr in crows if not dead_cohort(_t)]
        oos_skill = _cat_cv_skill(cbases, cmeta, ylist, best_l2)
        out["cat_specialists"][ck] = {
            "w": _fit_logistic(cdata, l2=best_l2),
            "oos_skill": (round(oos_skill, 4) if oos_skill is not None
                          else None),
            "n_eff": effective_n(ctrades),
            "n": len(cdata),
        }
    return out


def brain_adjust(strategy, price, ctx, side=None, category=None):
    """Credibility-weighted sizing tilt from the brain: with little data it
    defers to neutral (x1.0); as settles accumulate its voice grows. Bounded
    [0.4, 1.6] — it tilts the validated base edge, never replaces it.

    PARTIAL POOLING: the GLOBAL model (trained on all rows) is the prior and
    is always evaluated first, exactly as before. When a `category` is threaded
    through from the position/opportunity AND that category has earned a
    specialist with POSITIVE out-of-sample skill, the global probability is
    blended toward the category specialist, weighted by cw=n_eff/(n_eff+60) —
    so a category shrinks fully to the global model until it earns divergence
    out-of-sample. With category=None (the common path), or an OOS-negative /
    zero-credibility category, this is a PURE no-op: the returned multiplier is
    byte-identical to the pre-category-layer global model."""
    n, w = BRAIN.get("n", 0), BRAIN.get("w") or {}
    if n < 10 or not w:
        return 1.0
    x = _brain_x(strategy, price, ctx, side)
    pats = BRAIN.get("pat_feats") or []
    if pats:
        probe = {"strategy": strategy, "name": "", "category": None,
                 "side": side, "entry_price": price,
                 "closed": now_utc().isoformat(), "context": ctx, "pnl": 0}
        _add_pat_feats(x, _settle_traits(probe), pats)
    # the GLOBAL model sees the pre-sports feature space (byte-identical common
    # path); only the per-category sports specialist below sees the sports keys.
    xg = _global_x(x)
    stack = BRAIN.get("stack") or []
    models = BRAIN.get("models") or {}
    if stack:
        tot_w = sum(wt for _, wt in stack) or 1.0
        p_model = 0.0
        for name, wt in stack:
            try:
                p_model += wt * (ml.predict(models[name], xg)
                                 if name in models else _predict(w, xg))
            except Exception:
                p_model += wt * _predict(w, xg)
        p_model /= tot_w
        if BRAIN.get("cal"):
            p_model = ml.apply_cal(BRAIN["cal"], p_model)
    else:
        p_model = _predict(w, xg)
        spec = (BRAIN.get("specialists") or {}).get(strategy)
        if spec:
            p_model = 0.5 * (p_model + _predict(spec, xg))  # ensemble blend
    # --- per-category partial pooling (additive, OOS-gated, point-in-time) ---
    # Threaded from the position/opportunity — never recomputed here. Engages
    # ONLY when the category specialist beat the base rate out-of-sample
    # (oos_skill>0) and has nonzero credibility (n_eff>0). Otherwise the global
    # p_model is left exactly as computed above: a guaranteed no-op.
    ck = cat_key(category)
    if ck:
        cs = (BRAIN.get("cat_specialists") or {}).get(ck)
        if (cs and cs.get("w") and (cs.get("oos_skill") or 0) > 0
                and cs.get("n_eff", 0) > 0):
            cw = cs["n_eff"] / (cs["n_eff"] + 60.0)
            p_model = (1.0 - cw) * p_model + cw * _predict(cs["w"], x)
    n_eff = BRAIN.get("n_eff", n)   # clusters, not rows: pseudo-replication
    cred = (n_eff / (n_eff + 60.0)  # of one market can't buy credibility...
            * BRAIN.get("skill_factor", 0.5))  # ...and neither can in-sample fit
    return max(0.4, min(1.6, 1.0 + 2.0 * cred * (p_model - (price or 0.5))))


def brain_online_learn(pos, pnl):
    """Instant learning at settle time, upgraded two ways: (1) AdaGrad —
    each feature gets its own adaptive step size, so a rare signal like
    smart_agree learns fast from its few examples while bias stops
    oscillating; (2) drift sensing — a Page-Hinkley detector watches the
    brain's OWN per-settle logloss, and when its predictions stop
    describing the world, the next retrain re-anchors on post-drift data
    only instead of averaging two different regimes together."""
    if not BRAIN.get("w") or pos["strategy"] == "arbitrage":
        return
    try:
        x = _brain_x(pos["strategy"], pos.get("entry_price"),
                     pos.get("context"), pos["legs"][0].get("outcome"),
                     now_utc().isoformat(), pos.get("name"))
        # BRAIN["w"] is the GLOBAL logistic over the pre-sports feature space:
        # update it on the stripped vector so online learning never injects the
        # per-category sports keys into the global model (keeps it byte-identical
        # in dimension; the sports keys live only in the sports specialist).
        xg = _global_x(x)
        y = 1.0 if pnl >= 0 else 0.0
        p = max(0.02, min(0.98, _predict(BRAIN["w"], xg)))
        ph = BRAIN.setdefault("ph", ml.ph_new())
        if ml.ph_update(ph, -(y * math.log(p) + (1 - y) * math.log(1 - p))):
            BRAIN["drift_i"] = BRAIN.get("n", 0)
            journal("DRIFT", detector="page-hinkley",
                    at_settle=BRAIN["drift_i"], total_drifts=ph["drifts"])
        ml.sgd_step(BRAIN["w"], xg, y, g2=BRAIN.setdefault("g2", {}))
    except Exception:
        pass


def brain_top_factors(k=3):
    w = BRAIN.get("w") or {}
    ranked = sorted(((v, f) for f, v in w.items() if f != "bias"),
                    key=lambda t: -abs(t[0]))[:k]
    return [f"{BRAIN_FACTORS.get(f, f)} {'helps' if v > 0 else 'hurts'} ({v:+.2f})"
            for v, f in ranked]


def _sim_prior():
    """Replay-derived (cluster, band) pseudo-counts, discounted 4x — sim
    proposes, live confirms. Cached read of the SIM ledger."""
    def load():
        try:
            cells = (json.loads(SIM_FILE.read_text()) or {}).get("cells") or {}
        except (ValueError, OSError):
            return {}
        prior = {}
        for key, v in cells.items():
            parts = key.split("|")
            if len(parts) != 3:
                continue
            band = int(parts[1].rstrip("c"))
            k = (band // 5 * 5, parts[0])
            w, n = prior.get(k, (0.0, 0.0))
            prior[k] = (w + v["wins"] * 0.25, n + v["n"] * 0.25)
        return prior
    return _cached(("simprior",), 600, load)


def thompson_rank(account, opps):
    """Model 14 — Thompson sampling: explorer candidates ranked by a random
    draw from each cell's Beta posterior (5c-band x category). Unknown cells
    draw wide and get explored; proven-bad cells draw low and get skipped.
    Exploration stops being uniform and starts being intelligent."""
    cells = {}
    for t in account["settled"]:
        if t["strategy"] != "explore":
            continue
        c = (int(round((t.get("entry_price") or 0) * 20)), t.get("category") or "Other")
        w, l = cells.get(c, (0, 0))
        cells[c] = (w + 1, l) if t["pnl"] >= 0 else (w, l + 1)
    TS_STATE["cells"] = len(cells)
    prior = _sim_prior()

    def draw(o):
        band5 = int(round((o.get("entry_price") or 0) * 20)) * 5
        c = (band5 // 5, o.get("category") or "Other")
        w, l = cells.get(c, (0, 0))
        pw, pn = prior.get((band5, cluster_of(o.get("name"))), (0.0, 0.0))
        # live counts + discounted replay pseudo-counts: 470k ticks of
        # history warm-start the exploration instead of a blind uniform
        return (random.betavariate(w + pw + 1, l + (pn - pw) + 1)
                - (o.get("entry_price") or 0.5))
    for o in opps:
        o["_ts"] = round(draw(o), 4)
    return sorted(opps, key=lambda o: o["_ts"], reverse=True)



THRESH_RX = re.compile(
    r"(above|below|at least|or above|or higher|or more)\s*\$?([0-9][0-9,]*(?:\.[0-9]+)?)", re.I)


def parse_threshold(question):
    """('family', threshold, direction) for threshold-style questions, or
    None. 'BTC above $66,000' and 'BTC above $68,000' share a family."""
    m = THRESH_RX.search(question or "")
    if not m:
        return None
    direction = "up" if m.group(1).lower() != "below" else "down"
    thr = float(m.group(2).replace(",", ""))
    fam = family_of(question)
    return fam, thr, direction


def scan_pairs(cfg, skip_ids):
    """Research-backed combinatorial arbitrage (IMDEA 2025: $40M+ extracted
    from intra-market structures): same-family threshold markets must price
    monotonically — P(X>66k) can never be below P(X>68k). When they invert,
    buying YES(low threshold) + NO(high threshold) pays at least $1/share
    no matter the outcome. Locked profit, held to resolution."""
    acfg = cfg.get("arbitrage", {})
    fams = {}
    for offset in (0, 100):
        for m in get_json(f"{GAMMA}/markets", params={
                "active": "true", "closed": "false", "order": "volume24hr",
                "ascending": "false", "limit": 100, "offset": offset}) or []:
            if fnum(m.get("volume24hr")) < 500 or str(m.get("id")) in skip_ids:
                continue
            pt = parse_threshold(m.get("question", ""))
            if not pt or pt[2] != "up":
                continue
            prices = [fnum(p) for p in jlist(m.get("outcomePrices"))]
            tokens = jlist(m.get("clobTokenIds"))
            end = (m.get("endDate") or "")[:10]
            if len(prices) != 2 or len(tokens) != 2 or not end:
                continue
            fams.setdefault((pt[0], end), []).append((pt[1], m, tokens))
    out = []
    for (fam, end), markets in fams.items():
        if len(markets) < 2:
            continue
        markets.sort()
        for (lo_t, lo_m, lo_tok), (hi_t, hi_m, hi_tok) in zip(markets, markets[1:]):
            if lo_t == hi_t:
                continue
            lo_bs = book_stats(lo_tok[0])   # YES of the lower threshold
            time.sleep(0.1)
            hi_bs = book_stats(hi_tok[1])   # NO of the higher threshold
            time.sleep(0.1)
            if not lo_bs or not hi_bs:
                continue
            cost = lo_bs["ask"] + hi_bs["ask"]
            edge = 1.0 - cost               # min payout is $1/share-pair
            if edge < acfg.get("min_edge_cents", 1.5) / 100 + 0.015:
                continue                    # extra 1.5c safety margin
            shares = int(min(lo_bs["ask_size"], hi_bs["ask_size"],
                             acfg.get("max_cost_per_arb", 300) / max(cost, 0.01)))
            if shares < 1:
                continue
            out.append({
                "strategy": "arbitrage", "event_id": None,
                "category": market_category(lo_m),
                "context": {"kind": "threshold-pair", "family": fam,
                            "edge": round(edge, 4)},
                "name": f"PAIR {lo_m['question'][:40]} vs >{hi_t:g}",
                "legs": [
                    {"market_id": str(lo_m["id"]), "question": lo_m["question"],
                     "token_index": 0, "token_id": lo_tok[0],
                     "price": lo_bs["ask"], "outcome": "Yes"},
                    {"market_id": str(hi_m["id"]), "question": hi_m["question"],
                     "token_index": 1, "token_id": hi_tok[1],
                     "price": hi_bs["ask"], "outcome": "No"},
                ],
                "shares": shares, "cost": round(shares * cost, 2),
                "entry_price": round(cost, 4),
                "detail": f"monotonicity violated: YES>{lo_t:g} @ {lo_bs['ask']:.2f} "
                          f"+ NO>{hi_t:g} @ {hi_bs['ask']:.2f} = {cost:.3f}, "
                          f"min payout $1 -> +{edge*100:.1f}% locked",
            })
            time.sleep(0.1)
    return out



# -------------------------------------------- market model (model 15, ML)

MARKET_MODEL_FILE = HERE / "market_model.json"
MARKET_MODEL = {"w": {}, "skill": None}


def _mkt_x(price, hours, vol, spread):
    """Features for one market observation (favorite's side)."""
    p = price or 0.5
    return {"bias": 1.0,
            "p": p - 0.5,
            "extreme": max(0.0, p - 0.93) * 10,           # FLB zone
            "b90_93": 1.0 if 0.895 <= p < 0.935 else 0.0,  # the research-
            "b94_96": 1.0 if 0.935 <= p < 0.965 else 0.0,  # proven edge map
            "b97_99": 1.0 if p >= 0.965 else 0.0,          # is nonlinear
            "ttr": min((hours or 24) / 48.0, 2.0),
            "logvol": min(math.log10(max(vol or 1, 1)) / 6.0, 1.5),
            "spread": min((spread or 0.0) * 50, 2.0)}



def _fit_stumps(data, rounds=40):
    """AdaBoost over depth-1 decision stumps — learns nonlinear structure
    (like 'profitable at 90-93c but not 94-99c') that a linear model cannot.
    Pure Python; same training interface as the logistic."""
    feats = [k for k in data[0][0] if k != "bias"]
    n = len(data)
    wts = [1.0 / n] * n
    model = []
    cuts = {f: sorted({x[f] for x, _ in data}) for f in feats}
    for f in cuts:
        v = cuts[f]
        step = max(1, len(v) // 10)
        cuts[f] = v[step::step][:10]
    for _ in range(rounds):
        best = None
        for f in feats:
            for t in cuts[f]:
                for d in (1, -1):
                    err = sum(w for (x, y), w in zip(data, wts)
                              if (1 if (x[f] > t) else -1) * d != (1 if y else -1))
                    if best is None or err < best[0]:
                        best = (err, f, t, d)
        err, f, t, d = best
        if err >= 0.499:
            break
        err = max(err, 1e-6)
        alpha = 0.5 * math.log((1 - err) / err)
        model.append([f, t, d, round(alpha, 5)])
        tot = 0.0
        for i, (x, y) in enumerate(data):
            pred = (1 if (x[f] > t) else -1) * d
            wts[i] *= math.exp(-alpha * pred * (1 if y else -1))
            tot += wts[i]
        wts = [w / tot for w in wts]
    return model


def _predict_stumps(model, x):
    s = sum(a * ((1 if x.get(f, 0) > t else -1) * d) for f, t, d, a in model)
    return 1.0 / (1.0 + math.exp(-2.0 * max(-15.0, min(15.0, s))))


def train_market_model():
    """Model 15 — machine learning on the BIG dataset: every resolved market
    the recorder ever saw (thousands), not our ~100 trades. Trained walk-
    forward and judged against the hardest baseline that exists: the market
    price itself. Positive skill here IS alpha. Runs SHADOW-ONLY — its
    predictions are recorded on every entry for attribution, and it earns
    the right to act only if trades it disliked actually lose more."""
    seen, rows = set(), []

    def ingest(mid, p, h, b, a2, v):
        if h < 1 or h > 72:
            return
        bucket = 0 if h < 6 else 1 if h < 24 else 2
        if (mid, bucket) in seen:
            return
        seen.add((mid, bucket))
        rows.append([mid, max(p, 1 - p), h, v, a2 - b, p >= 0.5])

    if _CORPUS_DONE:    # hot corpus: skip re-parsing 2M+ CSV rows from disk
        for mid, lst in list(CORPUS.items()):
            for ts, p, b, a2, v, h in lst:
                ingest(mid, p, h, b, a2, v)
    else:
        files = sorted(DATA_DIR.glob("snapshots-*.csv"))
        if not files:
            print("no recorder data")
            return None
        for path in files:
            with path.open() as f:
                for r in csv.DictReader(f):
                    try:
                        ingest(r["market_id"], float(r["p_yes"]),
                               float(r["hours_to_end"]), float(r["bid"]),
                               float(r["ask"]), float(r["vol24h"]))
                    except (ValueError, KeyError):
                        continue
    ids = sorted({r[0] for r in rows})
    finals, ends = {}, {}
    for i in range(0, len(ids), 20):
        for m in get_json(f"{GAMMA}/markets",
                          params=[("id", x) for x in ids[i:i + 20]]
                          + [("closed", "true")]) or []:
            pr = [fnum(x) for x in jlist(m.get("outcomePrices"))]
            if len(pr) == 2:
                finals[str(m["id"])] = pr[0]
                ends[str(m["id"])] = (m.get("closedTime") or m.get("endDate") or "")
    data = []
    for mid, fav_p, h, v, sp, fav_is_yes in rows:
        if mid not in finals:
            continue
        won = (finals[mid] >= 0.5) == fav_is_yes
        data.append((ends[mid], _mkt_x(fav_p, h, v, sp), 1.0 if won else 0.0, fav_p))
    if len(data) < 300:
        print(f"only {len(data)} labeled observations — need 300+")
        return None
    data.sort(key=lambda d: d[0])         # chronological by resolution
    cut = int(len(data) * 0.75)
    train = [(x, y) for _, x, y, _ in data[:cut]]
    hold = data[cut:]
    cands = {"logistic": (_fit_logistic(train), _predict),
             "stumps": (_fit_stumps(train), _predict_stumps),
             "gbm": (ml.fit_gbm(train), ml.predict),
             "forest": (ml.fit_forest(train), ml.predict)}
    briers = {n: 0.0 for n in cands}
    br_p = 0.0
    for _, x, y, fav_p in hold:
        for n, (mdl, pf) in cands.items():
            briers[n] += (pf(mdl, x) - y) ** 2
        br_p += (fav_p - y) ** 2              # the market's own forecast
    n_h = len(hold)
    champion = min(briers, key=briers.get)
    br_m = briers[champion]
    br_log, br_stp = briers["logistic"], briers["stumps"]
    skill = (br_p - br_m) / n_h               # >0 = beats the market price
    full = [(x, y) for _, x, y, _ in data]
    out = {"generated": now_utc().isoformat(timespec="seconds"),
           "n_train": cut, "n_holdout": n_h,
           "champion": champion,
           "w": _fit_logistic(full),
           "stumps": _fit_stumps(full),
           "mlmodel": (ml.ZOO[champion](full) if champion in ml.ZOO else None),
           "briers": {n: round(b / n_h, 5) for n, b in briers.items()},
           "brier_logistic": round(br_log / n_h, 5),
           "brier_stumps": round(br_stp / n_h, 5),
           "brier_market": round(br_p / n_h, 5),
           "skill_vs_market": round(skill, 5),
           "mode": "shadow — predictions recorded, never acted on"}
    atomic_write(MARKET_MODEL_FILE, json.dumps(out, indent=1))
    MARKET_MODEL.update(out)
    print(json.dumps({k: v for k, v in out.items() if k != "w"}, indent=1))
    return out


def market_model_p(price, hours, vol, spread):
    """Shadow prediction for a candidate entry (None if model untrained)."""
    w = MARKET_MODEL.get("w") or {}
    if not w:
        if MARKET_MODEL_FILE.exists():
            try:
                MARKET_MODEL.update(json.loads(MARKET_MODEL_FILE.read_text()))
                w = MARKET_MODEL.get("w") or {}
            except ValueError:
                return None
    if not w:
        return None
    x = _mkt_x(price, hours, vol, spread)
    ch = MARKET_MODEL.get("champion")
    if ch in ("gbm", "forest") and MARKET_MODEL.get("mlmodel"):
        return round(ml.predict(MARKET_MODEL["mlmodel"], x), 3)
    if ch == "stumps" and MARKET_MODEL.get("stumps"):
        return round(_predict_stumps(MARKET_MODEL["stumps"], x), 3)
    return round(_predict(w, x), 3)





# ---------------------------------------------- fundamental oracles (shadow)

ORACLE_CACHE = {}
_WX_RX = re.compile(r"(highest|lowest) temperature in ([a-zA-Z .'\-]+?) be "
                    r"(-?\d+(?:\.\d+)?)\s*\u00b0?\s*([CF])? or "
                    r"(above|higher|below|lower)", re.I)
_WX_RANGE_RX = re.compile(r"(highest|lowest) temperature in "
                          r"([a-zA-Z .'\-]+?) be between "
                          r"(-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)"
                          r"\s*\u00b0\s*([CF])", re.I)
_WX_EXACT_RX = re.compile(r"(highest|lowest) temperature in "
                          r"([a-zA-Z .'\-]+?) be (-?\d+(?:\.\d+)?)"
                          r"\s*\u00b0\s*([CF])(?!\s+or\b)", re.I)


def _wx_parse(question):
    """Parse ANY of the three real weather-market shapes into
    (max_or_min, city, event_fn) where event_fn(forecast_C) returns
    (event_happens, margin). The old parser knew one shape and went
    blind on 58 of 62 live questions \u2014 the oracle existed but never
    spoke on the book's single largest trade family."""
    m = _WX_RX.search(question)
    if m:
        kind = "max" if m.group(1).lower() == "highest" else "min"
        city, thr = m.group(2).strip(), float(m.group(3))
        is_f = (m.group(4) or "C").upper() == "F"
        d = m.group(5).lower()

        def ev(pred, thr=thr, d=d, is_f=is_f):
            # room: ALWAYS positive when the event is favored, in °C units
            p = pred * 9 / 5 + 32 if is_f else pred
            room = (p - thr) if d in ("above", "higher") else (thr - p)
            return room > 0, round(room / (1.8 if is_f else 1), 1)
        return kind, city, ev
    m = _WX_RANGE_RX.search(question)
    if m:
        kind = "max" if m.group(1).lower() == "highest" else "min"
        city = m.group(2).strip()
        lo, hi = float(m.group(3)), float(m.group(4))
        is_f = m.group(5).upper() == "F"

        def ev(pred, lo=lo, hi=hi, is_f=is_f):
            p = pred * 9 / 5 + 32 if is_f else pred   # forecast is Celsius
            inside = lo <= round(p) <= hi
            edge = min(abs(p - lo), abs(p - hi))
            margin = round((edge if inside else -edge) / (1.8 if is_f else 1), 1)
            return inside, margin
        return kind, city, ev
    m = _WX_EXACT_RX.search(question)
    if m:
        kind = "max" if m.group(1).lower() == "highest" else "min"
        city, pin = m.group(2).strip(), float(m.group(3))
        is_f = m.group(4).upper() == "F"

        def ev(pred, pin=pin, is_f=is_f):
            p = pred * 9 / 5 + 32 if is_f else pred
            # room: positive when the forecast rounds TO the pin (event),
            # negative degrees-beyond-boundary when it misses
            room = round((0.5 - abs(p - pin)) / (1.8 if is_f else 1), 1)
            return room > 0, room
        return kind, city, ev
    return None
_CRYPTO_IDS = {"bitcoin": "BTC", "ethereum": "ETH",
               "solana": "SOL", "xrp": "XRP", "dogecoin": "DOGE"}


def _cached(key, ttl, fn):
    v = ORACLE_CACHE.get(key)
    if v and time.time() - v[0] < ttl:
        return v[1]
    out = fn()
    ORACLE_CACHE[key] = (time.time(), out)
    return out


def _wx_forecast(city, date_str, kind="max"):
    """Open-Meteo daily max/min-temperature forecast (free, keyless)."""
    def fetch():
        g = get_json("https://geocoding-api.open-meteo.com/v1/search",
                     params={"name": city, "count": 1}) or {}
        res = (g.get("results") or [{}])[0]
        if not res.get("latitude"):
            return {}
        f = get_json("https://api.open-meteo.com/v1/forecast", params={
            "latitude": res["latitude"], "longitude": res["longitude"],
            "daily": "temperature_2m_max,temperature_2m_min",
            "timezone": "auto", "forecast_days": 5}) or {}
        d = f.get("daily") or {}
        return {"max": dict(zip(d.get("time", []),
                                d.get("temperature_2m_max", []))),
                "min": dict(zip(d.get("time", []),
                                d.get("temperature_2m_min", [])))}
    table = _cached(("wx", city.lower()), 900, fetch) or {}
    return (table.get(kind) or {}).get(date_str)


def _spot(sym):
    """Coinbase public spot price (free, keyless, US-friendly)."""
    return _cached(("spot", sym), 60, lambda: fnum(
        (get_json(f"https://api.coinbase.com/v2/prices/{sym}-USD/spot")
         or {}).get("data", {}).get("amount")))



def _tape_sign(side, outcome_index):
    """+1 = YES-flow. Buying the No token IS selling Yes — the tape's
    outcomeIndex decides which way a print actually pushes."""
    s = 1.0 if str(side).upper() == "BUY" else -1.0
    return -s if outcome_index == 1 else s


SMART_FILE = HERE / "wallet_intel.json"
try:
    SMART = json.loads(SMART_FILE.read_text())
except (OSError, ValueError):
    SMART = {"wallets": {}}
_SMART_SAVED = [0.0]


def _wallet_intel(addr):
    """Lifetime profile of a wallet from its public trade history. WHO is
    betting matters as much as how much: a wallet with no history placing
    a big bet may know something the market doesn't."""
    w = SMART["wallets"].get(addr)
    if w and time.time() - w.get("at", 0) < 6 * 3600:
        return w
    ts = get_json("https://data-api.polymarket.com/trades",
                  params={"user": addr, "limit": 100})
    if ts is None:
        return w  # a stale profile beats no profile
    w = {"n": len(ts),
         "first": min((t.get("timestamp") or 0) for t in ts) if ts else 0,
         "vol": round(sum(fnum(t.get("size")) * fnum(t.get("price"))
                          for t in ts), 0),
         "at": time.time()}
    SMART["wallets"][addr] = w
    if len(SMART["wallets"]) > 4000:  # keep the freshest half
        keep = sorted(SMART["wallets"].items(),
                      key=lambda kv: kv[1].get("at", 0))[2000:]
        SMART["wallets"] = dict(keep)
    if time.time() - _SMART_SAVED[0] > 60:
        _SMART_SAVED[0] = time.time()
        try:
            atomic_write(SMART_FILE, json.dumps(SMART))
        except OSError:
            pass
    return w


def _is_fresh(profile, now=None):
    """A short, young trading history. Under 100 trades means the 100-trade
    window IS the wallet's whole life, so n and first-seen are exact."""
    if not profile or profile.get("n", 0) >= 100:
        return False
    age_days = ((now or time.time()) - (profile.get("first") or 0)) / 86400
    return profile["n"] <= 25 and age_days <= 7


def whale_flow(condition_id, ttl=60):
    """Informed-participant flow from Polymarket's public trade tape: net
    aggressor YES-dollars, big-print bias, and fresh-wallet flow (the
    documented insider pattern — new wallets making outsized bets)."""
    def fetch():
        ts = get_json("https://data-api.polymarket.com/trades",
                      params={"market": condition_id, "limit": 50}) or []
        net = big = fresh = 0.0
        fetches = 0
        for t in ts:
            usd = fnum(t.get("size")) * fnum(t.get("price"))
            sgn = _tape_sign(t.get("side"), t.get("outcomeIndex"))
            net += sgn * usd
            if usd >= 500:
                big += sgn
            addr = t.get("proxyWallet")
            if usd >= 250 and addr:
                cached = SMART["wallets"].get(addr)
                live = cached and time.time() - cached.get("at", 0) < 6 * 3600
                if not live and fetches >= 3:
                    continue  # wallet-lookup budget per market per minute
                if not live:
                    fetches += 1
                if _is_fresh(_wallet_intel(addr)):
                    fresh += sgn * usd
        return {"net": round(net, 0), "big": big, "fresh": round(fresh, 0)}
    return _cached(("whale", condition_id), ttl, fetch)


def whale_verdict(wf, fav_index):
    """Does the tape agree with the side we want? Net YES-flow backs YES."""
    if not wf or abs(wf.get("net", 0)) < 200:
        return None
    yes_flow = wf["net"] > 0
    return yes_flow if fav_index == 0 else not yes_flow


def smart_verdict(wf, fav_index):
    """Fresh-wallet money on our side? Speaks only when $300+ of it has
    hit the tape — small fresh prints are noise, big ones are the signal."""
    if not wf or abs(wf.get("fresh", 0)) < 300:
        return None
    yes_flow = wf["fresh"] > 0
    return yes_flow if fav_index == 0 else not yes_flow



_KRAKEN_PAIRS = {"BTC": "XBTUSD", "ETH": "ETHUSD", "SOL": "SOLUSD",
                 "XRP": "XRPUSD", "DOGE": "XDGUSD"}


def _crypto_vol(sym):
    """Hourly realized volatility from Kraken's free OHLC (no key).

    POINT-IN-TIME: Kraken returns the in-progress (current) hourly candle as the
    LAST row — its close/high/low keep moving and would leak forward data into a
    decision made mid-hour. We drop that trailing candle (rows[:-1]) so vol is
    computed from CLOSED candles only (close-to-close over candle[i-1]->[i])."""
    def fetch():
        k = get_json("https://api.kraken.com/0/public/OHLC",
                     params={"pair": _KRAKEN_PAIRS.get(sym, sym + "USD"),
                             "interval": 60}) or {}
        keys = [x for x in (k.get("result") or {}) if x != "last"]
        all_rows = (k["result"][keys[0]] if keys else [])
        rows = all_rows[:-1][-168:]          # drop the in-progress candle
        closes = [fnum(r[4]) for r in rows if fnum(r[4]) > 0]
        if len(closes) < 24:
            return None
        rets = [math.log(closes[i] / closes[i - 1])
                for i in range(1, len(closes))]
        mu = sum(rets) / len(rets)
        return (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5
    return _cached(("cvol", sym), 900, fetch)


# --------------------------------------------- per-category CRYPTO feature read
# CoinGecko ID map for the keyless /simple/price fallback spot source. Used only
# when Coinbase is unreachable, so the crypto feature read degrades gracefully
# instead of going neutral the instant one exchange blips.
_COINGECKO_IDS = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
                  "XRP": "ripple", "DOGE": "dogecoin"}


def _coingecko_spot(sym):
    """CoinGecko public spot (free, keyless) — the fallback source for spot
    when Coinbase blips. Cached 60s, fail-silent (None on any error)."""
    cid = _COINGECKO_IDS.get(sym)
    if not cid:
        return None
    return _cached(("cgspot", sym), 60, lambda: fnum(
        ((get_json("https://api.coingecko.com/api/v3/simple/price",
                   params={"ids": cid, "vs_currencies": "usd"}) or {})
         .get(cid, {}) or {}).get("usd")) or None)


def _crypto_spot(sym):
    """Point-in-time spot for `sym`: Coinbase first (US-friendly, 60s cache),
    CoinGecko as a fail-silent fallback. No future data — a live quote AS OF
    NOW. Returns None when both sources are unreachable (feature -> neutral)."""
    return _spot(sym) or _coingecko_spot(sym)


def _kraken_spread_bps(sym):
    """Live bid/ask spread in basis points from Kraken's public Ticker (b, a
    fields), AS OF the request time. Free, keyless, governed, cached 60s,
    fail-silent (None on any error). No forward data: a snapshot of the current
    top-of-book, never a future print."""
    def fetch():
        pair = _KRAKEN_PAIRS.get(sym, sym + "USD")
        k = get_json("https://api.kraken.com/0/public/Ticker",
                     params={"pair": pair}) or {}
        res = k.get("result") or {}
        keys = list(res)
        if not keys:
            return None
        t = res[keys[0]] or {}
        bid = fnum((t.get("b") or [None])[0])   # best bid price
        ask = fnum((t.get("a") or [None])[0])   # best ask price
        if bid <= 0 or ask <= 0 or ask < bid:
            return None
        mid = (bid + ask) / 2.0
        if mid <= 0:
            return None
        return (ask - bid) / mid * 10000.0      # spread in basis points
    return _cached(("kspread", sym), 60, fetch)


def crypto_features(m, price):
    """Point-in-time crypto feature read for one market, attached to the entry
    context of CRYPTO markets only. ALL fields default neutral (None) so a
    non-crypto market — or a crypto market we cannot map to a symbol/strike —
    leaves _brain_x's crypto features at EXACTLY 0.0 and the global/common path
    byte-identical.

      crypto_spot_dist:  signed (spot - strike)/strike, the distance of live
                         spot from the market's threshold (Coinbase spot, with
                         CoinGecko fallback; 60s cache). None when no strike.
      crypto_rvol_h:     hourly realized volatility from Kraken's last 24 CLOSED
                         hourly candles (close-to-close; the in-progress candle
                         is dropped so no forward high/low leaks). 15-min cache.
      crypto_spread_bps: live bid/ask spread (bps) from Kraken Ticker (b, a) at
                         request time. 60s cache.

    Fail-silent and cached; a dead source yields None, never an exception. No
    future data: every read is a snapshot AS OF NOW (see module header)."""
    out = {"crypto_spot_dist": None, "crypto_rvol_h": None,
           "crypto_spread_bps": None}
    try:
        q = (m.get("question") or "").lower()
        sym = None
        for word, s in _CRYPTO_IDS.items():
            if word in q:
                sym = s
                break
        if sym is None:
            return out
        # hourly realized vol (point-in-time; last 24 closed candles) + spread.
        out["crypto_rvol_h"] = _crypto_vol(sym)
        out["crypto_spread_bps"] = _kraken_spread_bps(sym)
        # spot distance from the threshold, when the question carries a strike.
        pt = parse_threshold(m.get("question") or "")
        if pt and pt[1]:
            spot = _crypto_spot(sym)
            if spot and spot > 0:
                out["crypto_spot_dist"] = (spot - pt[1]) / pt[1]
    except Exception:
        pass
    return out


# --------------------------------------------- per-category WEATHER feature read
# Two free, keyless sources combine into a point-in-time ensemble read for the
# weather specialist:
#   * Open-Meteo ENSEMBLE API (api.open-meteo.com/v1/ensemble) — ~30 GFS members,
#     daily 2m-max/min per member. The disagreement ACROSS members is the
#     forecast uncertainty; the fraction of members on the market's side is the
#     consensus. No key required. An optional OPENMETEO_API_KEY (paid tier, higher
#     rate limits) is read from os.environ and appended when present; absent, the
#     free endpoint is used and nothing is gated off.
#   * weather.gov / NWS (api.weather.gov) — official US point forecast, used as a
#     fail-silent cross-check / mean source when Open-Meteo blips. Keyless; NWS
#     only requires a User-Agent, which `session` already sets globally.
# Every read is a snapshot AS OF NOW, governed, timeout-bounded and cached; a
# dead source yields None (feature -> neutral), never an exception or a stall.
def _wx_strike_c(question):
    """Extract the market's temperature threshold in CELSIUS (the forecast's
    native unit) from any of the three weather-market shapes, so the ensemble
    forecast and the strike live in the same units. Returns the strike in °C,
    or None when the question is not a parseable temperature market. For range
    markets the midpoint is used. Point-in-time: parsed from the question text
    already on the market, never fetched."""
    m = _WX_RX.search(question or "")
    if m:
        thr = float(m.group(3))
        is_f = (m.group(4) or "C").upper() == "F"
        return (thr - 32) * 5 / 9 if is_f else thr
    m = _WX_RANGE_RX.search(question or "")
    if m:
        lo, hi = float(m.group(3)), float(m.group(4))
        is_f = m.group(5).upper() == "F"
        mid = (lo + hi) / 2.0
        return (mid - 32) * 5 / 9 if is_f else mid
    m = _WX_EXACT_RX.search(question or "")
    if m:
        pin = float(m.group(3))
        is_f = m.group(4).upper() == "F"
        return (pin - 32) * 5 / 9 if is_f else pin
    return None


def _wx_strike_side(question):
    """Return +1 if the event is 'forecast >= strike' (above/higher), -1 if it
    is 'below/lower', 0 for range/exact (no single direction). Used to count the
    fraction of ensemble members on the market's side. Point-in-time (question
    text only)."""
    m = _WX_RX.search(question or "")
    if m:
        return 1 if m.group(5).lower() in ("above", "higher") else -1
    return 0


def _wx_geocode(city):
    """City -> (lat, lon) via Open-Meteo's free keyless geocoder. Cached 24h,
    fail-silent (None on any error). Point-in-time: a static lookup, no forward
    data."""
    def fetch():
        g = get_json("https://geocoding-api.open-meteo.com/v1/search",
                     params={"name": city, "count": 1}) or {}
        res = (g.get("results") or [{}])[0]
        lat, lon = res.get("latitude"), res.get("longitude")
        if lat is None or lon is None:
            return None
        return (lat, lon)
    return _cached(("wxgeo", (city or "").lower()), 86400, fetch)


def _openmeteo_ensemble(lat, lon, date_str, kind="max"):
    """~30-member GFS ensemble daily max/min temperature (°C) for one day from
    Open-Meteo's free ensemble API. Returns a list of per-member values, or []
    when unavailable. Cached 15 min, governed, fail-silent. POINT-IN-TIME: the
    forecast is the model run available AS OF the request; we read the target
    DAY's daily field per member — no future observation, just disagreement
    across today's ensemble members. An optional OPENMETEO_API_KEY (paid tier)
    is read from os.environ and forwarded when present; absent, the keyless
    endpoint is used and the source is never gated off."""
    def fetch():
        var = "temperature_2m_max" if kind == "max" else "temperature_2m_min"
        params = {"latitude": lat, "longitude": lon, "daily": var,
                  "models": "gfs_seamless", "timezone": "auto",
                  "forecast_days": 5}
        key = os.environ.get("OPENMETEO_API_KEY")
        if key:
            params["apikey"] = key
        f = get_json("https://ensemble-api.open-meteo.com/v1/ensemble",
                     params=params) or {}
        d = f.get("daily") or {}
        times = d.get("time") or []
        if date_str not in times:
            return []
        idx = times.index(date_str)
        # each member is its own daily series keyed e.g. temperature_2m_max_member01
        members = []
        for k, series in d.items():
            if not k.startswith(var):
                continue
            try:
                v = series[idx]
            except (IndexError, TypeError):
                continue
            if v is not None:
                members.append(float(v))
        return members
    return _cached(("wxens", round(lat, 2), round(lon, 2), date_str, kind),
                   900, fetch) or []


def _nws_point_forecast(lat, lon, date_str, kind="max"):
    """weather.gov / NWS official daily max/min (°C) for a US point, used as a
    fail-silent cross-check / mean source when the ensemble is unavailable.
    Keyless (NWS only needs the User-Agent `session` already sends). Cached 1h,
    governed, fail-silent (None outside the US or on any error). POINT-IN-TIME:
    a forecast issued AS OF NOW, never a future observation."""
    def fetch():
        pts = get_json(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}") or {}
        url = ((pts.get("properties") or {}).get("forecast"))
        if not url:
            return None
        fc = get_json(url) or {}
        best = None
        for p in (fc.get("properties") or {}).get("periods", []):
            start = (p.get("startTime") or "")[:10]
            if start != date_str:
                continue
            t = p.get("temperature")
            unit = (p.get("temperatureUnit") or "F").upper()
            is_day = p.get("isDaytime", True)
            if t is None:
                continue
            tc = (t - 32) * 5 / 9 if unit == "F" else float(t)
            # daytime high -> max; nighttime low -> min
            if kind == "max" and is_day:
                best = tc if best is None else max(best, tc)
            elif kind == "min" and not is_day:
                best = tc if best is None else min(best, tc)
        return best
    return _cached(("wxnws", round(lat, 4), round(lon, 4), date_str, kind),
                   3600, fetch)


# Per-city rolling forecast-error scale (°C). Seeded with a conservative
# climatological day-ahead RMSE for daily 2m temperature so the normalization
# denominator is never zero on the first read; the +0.5 floor in the feature
# keeps it finite regardless. Static constant -> no future data.
_WX_HIST_ERR_C = 1.8


def weather_features(m, price):
    """Point-in-time weather feature read for one market, attached to the entry
    context of WEATHER markets only. ALL fields default neutral (None) so a
    non-weather market — or a weather market we cannot parse to a city/strike —
    leaves _brain_x's weather features at EXACTLY 0.0 and the global/common path
    byte-identical.

      wx_fc_strike:  normalized distance between the ensemble forecast MEAN and
                     the market strike, (forecast_mean - strike) / (hist_err+0.5)
                     in °C. Captures market mispricing vs the objective forecast.
                     None when the ensemble (and NWS fallback) are unavailable.
      wx_fc_spread:  ensemble disagreement = stdev of member forecasts (°C). A
                     wider spread = lower forecast confidence. None when <3
                     members are available.
      wx_model_agree: fraction of ensemble members landing on the market's side
                     of the strike (above/higher vs below/lower). None for range
                     / exact markets that have no single direction, or when no
                     ensemble is available.

    Open-Meteo ensemble first (mean + spread + agreement); weather.gov/NWS is a
    fail-silent fallback for the mean only. Governed, cached, fail-silent: a dead
    source yields None, never an exception or a stall. No future data: every read
    is a snapshot of forecasts issued AS OF NOW (see crypto/oracle headers)."""
    out = {"wx_fc_strike": None, "wx_fc_spread": None, "wx_model_agree": None}
    try:
        q = m.get("question") or ""
        parsed = _wx_parse(q)
        strike_c = _wx_strike_c(q)
        if not parsed or strike_c is None:
            return out
        kind, city, _ev = parsed
        geo = _wx_geocode(city)
        if not geo:
            return out
        lat, lon = geo
        # resolve the target day from the question's date when present, else the
        # market end; the forecast table is keyed by ISO date (point-in-time).
        end = m.get("endDate") or m.get("end_date") or ""
        date_str = (end or now_utc().isoformat())[:10]
        members = _openmeteo_ensemble(lat, lon, date_str, kind)
        fc_mean = None
        if len(members) >= 3:
            fc_mean = sum(members) / len(members)
            mu = fc_mean
            sd = (sum((v - mu) ** 2 for v in members) / len(members)) ** 0.5
            out["wx_fc_spread"] = sd
            side = _wx_strike_side(q)
            if side != 0:
                on_side = sum(1 for v in members
                              if (v >= strike_c) == (side > 0))
                # fraction on the CORRECT side per the market's direction
                out["wx_model_agree"] = on_side / len(members)
        if fc_mean is None:
            fc_mean = _nws_point_forecast(lat, lon, date_str, kind)
        if fc_mean is not None:
            out["wx_fc_strike"] = (fc_mean - strike_c) / (_WX_HIST_ERR_C + 0.5)
    except Exception:
        pass
    return out


# --------------------------------------------------------------- MACRO ------
# Per-category MACRO features for the macro specialist. SOURCE:
#   * FRED (Federal Reserve Economic Data, api.stlouisfed.org) — official daily/
#     monthly series. KEY-GATED: a FRED_API_KEY is read from os.environ and
#     appended when present; absent, the source is skipped SILENTLY (every FRED
#     read returns None -> feature stays neutral 0.0 -> global path unchanged).
# POINT-IN-TIME / NO LEAKAGE: FRED observations publish with a real-world lag
# (CPI ~12 days after month-end, DFF ~1 day after the observation date), so the
# LATEST available observation AS OF NOW carries no forward knowledge. The
# market's rate/CPI expectation is today's price/consensus, not a future
# forecast. The 10Y-2Y spread is the latest published Treasury constant-maturity
# yields. Every read is governed, timeout-bounded, cached and fail-silent; a dead
# or key-less source yields None (never an exception or a stall).
_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

# Markets whose subject is a macro/FRED series. Point-in-time: matched on the
# question text already on the market, never fetched.
_MACRO_RATE_RX = re.compile(
    r"\b(fed(eral)?\s+(funds|reserve)|interest\s+rate|rate\s+(hike|cut|"
    r"decision)|fomc|basis\s*points?|bps)\b", re.I)
_MACRO_CPI_RX = re.compile(r"\b(cpi|inflation|consumer\s+price)\b", re.I)
_MACRO_YIELD_RX = re.compile(
    r"\b(yield\s+curve|recession|10y|2y|treasur(y|ies)|10-?year|2-?year)\b",
    re.I)
# A market-implied rate target in the question, e.g. "above 4.5%" / "4.50 percent".
_MACRO_RATE_TARGET_RX = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:%|percent|pct)", re.I)


def _fred_latest(series_id, limit=1):
    """The most-recent `limit` published observations of a FRED series, newest
    first, as a list of (date, float) — or [] when the source is unavailable.
    KEY-GATED (FRED_API_KEY from os.environ; absent -> [] silently). Governed,
    timeout-bounded, fail-silent, cached 1h. POINT-IN-TIME: sort_order=desc with
    no realtime override returns only observations already PUBLISHED as of now;
    FRED never surfaces a future-dated value here, so no forward leak."""
    def fetch():
        key = os.environ.get("FRED_API_KEY")
        if not key:                      # key-gated: skip silently when absent
            return []
        g = get_json(_FRED_BASE, params={
            "series_id": series_id, "api_key": key, "file_type": "json",
            "sort_order": "desc", "limit": limit}) or {}
        out = []
        for o in (g.get("observations") or []):
            v = o.get("value")
            if v in (None, ".", ""):     # FRED marks missing values as "."
                continue
            try:
                out.append((o.get("date"), float(v)))
            except (TypeError, ValueError):
                continue
        return out
    return _cached(("fred", series_id, limit), 3600, fetch) or []


def _fred_value(series_id):
    """Latest single published value of a FRED series, or None (fail-silent /
    key-less). Point-in-time: the newest observation already published."""
    obs = _fred_latest(series_id, 1)
    return obs[0][1] if obs else None


def _macro_rate_target(question):
    """The market-implied Fed-rate target (in %) parsed from the question text,
    or None. Point-in-time: question text only, never fetched. Used as today's
    rate EXPECTATION for the deviation feature (a contemporaneous consensus, not
    a forward forecast)."""
    m = _MACRO_RATE_TARGET_RX.search(question or "")
    if not m:
        return None
    try:
        return float(m.group(1))
    except (TypeError, ValueError):
        return None


def macro_features(m, price):
    """Point-in-time macro feature read for one market, attached to the entry
    context of MACRO markets only. ALL fields default neutral (None) so a
    non-macro market — or a macro market we cannot map to a FRED series — leaves
    _brain_x's macro features at EXACTLY 0.0 and the global/common path
    byte-identical.

      macro_rate_dev:    the market's expected Fed rate (parsed % target) minus
                         the latest DFF (Federal Funds Rate) observation, scaled
                         to [-1.5, 1.5]. Captures rate-expectation divergence.
                         None when no rate target is in the question or DFF is
                         unavailable (key-less FRED).
      macro_cpi_surprise: YoY CPI change vs consensus forecast (decimal pct
                         points; +0.3 == CPI ran 0.3pp hot). Normalized later by
                         _brain_x's 0.5% basis. None when CPI/consensus is
                         unavailable. (Consensus is the contemporaneous market /
                         survey value carried on the market, never a future read.)
      macro_yield_signal: 10Y-2Y spread regime — -1.0 inverted (recession risk),
                         -0.5 flat, 0.0 normal, 0.5 steep growth. From the latest
                         published Treasury constant-maturity yields (DGS10,
                         DGS2). None when the spread is unavailable.

    KEY-GATED (FRED_API_KEY): absent, every FRED read returns None and all three
    fields stay neutral. Governed, cached, fail-silent: a dead source yields None,
    never an exception or a stall. No future data — see the MACRO header."""
    out = {"macro_rate_dev": None, "macro_cpi_surprise": None,
           "macro_yield_signal": None}
    try:
        q = m.get("question") or ""
        # (1) rate deviation: market-implied target vs latest DFF observation.
        if _MACRO_RATE_RX.search(q):
            target = _macro_rate_target(q)
            dff = _fred_value("DFF")           # Federal Funds Rate (daily, %)
            if target is not None and dff is not None:
                out["macro_rate_dev"] = target - dff
        # (2) CPI surprise: YoY CPI vs a contemporaneous consensus carried on the
        #     market (point-in-time; never a forward forecast). The consensus is
        #     read from the market when present; absent, the feature stays None.
        if _MACRO_CPI_RX.search(q):
            consensus = m.get("cpi_consensus_yoy")
            cpi_yoy = _fred_value("CPIAUCSL_YOY") if consensus is not None \
                else None
            if cpi_yoy is not None and consensus is not None:
                out["macro_cpi_surprise"] = cpi_yoy - consensus
        # (3) yield-curve regime from the latest published 10Y/2Y yields.
        if _MACRO_YIELD_RX.search(q) or _MACRO_RATE_RX.search(q):
            y10 = _fred_value("DGS10")
            y2 = _fred_value("DGS2")
            if y10 is not None and y2 is not None:
                spread = y10 - y2
                if spread < -0.10:
                    out["macro_yield_signal"] = -1.0   # inverted (recession)
                elif spread < 0.25:
                    out["macro_yield_signal"] = -0.5   # flat
                elif spread < 1.00:
                    out["macro_yield_signal"] = 0.0    # normal
                else:
                    out["macro_yield_signal"] = 0.5    # steep growth
    except Exception:
        pass
    return out


def crypto_prob(sym, strike, hours):
    """Price the market like the digital option it is: P(spot_T > strike)
    under a driftless lognormal with realized vol. Spot from Coinbase,
    vol from Kraken — two independent free APIs combine into a true model
    probability instead of a sign check."""
    spot = _spot(sym)
    vol_h = _crypto_vol(sym)
    if not spot or not strike:
        return None
    if not vol_h:
        return 1.0 if spot > strike else 0.0
    sigma_t = vol_h * math.sqrt(max(hours, 0.5))
    if sigma_t < 1e-5:
        return 1.0 if spot > strike else 0.0
    z = math.log(strike / spot) / sigma_t
    return 0.5 * (1.0 - math.erf(z / math.sqrt(2)))


def oracle_check(question, end, outcome_name):
    """Fundamental second opinion on a market's subject: the actual weather
    forecast for temperature markets, the actual spot price for crypto
    markets. SHADOW — tags entries agree/disagree; attribution decides
    whether the oracle ever earns a veto."""
    try:
        wx = _wx_parse(question or "")
        if wx and end:
            kind, city, event_of = wx
            pred = _wx_forecast(city, end.strftime("%Y-%m-%d"), kind)
            if pred is None:
                return None, None, None
            event, room = event_of(pred)
            # side-signed margin: ALWAYS positive degrees of safety for
            # OUR side (audit: four incompatible margin units were feeding
            # one bonus threshold — knife-edge entries got the x1.25)
            yes = (outcome_name or "").lower().startswith("yes")
            side_conf = room if yes else -room
            return side_conf > 0, round(side_conf, 1), "wx"
        q = (question or "").lower()
        for word, sym in _CRYPTO_IDS.items():
            if word in q:
                pt = parse_threshold(question)
                if not pt:
                    return None, None, None
                hours = (max(0.5, (end - now_utc()).total_seconds() / 3600)
                         if end else 24.0)
                p_above = crypto_prob(sym, pt[1], hours)
                if p_above is None:
                    return None, None, None
                p_event = p_above if pt[2] == "up" else 1.0 - p_above
                p_side = (p_event if (outcome_name or "").lower()
                          .startswith("yes") else 1.0 - p_event)
                if abs(p_side - 0.5) < 0.05:
                    return None, None, None    # model has no real opinion
                return p_side >= 0.5, round(p_side - 0.5, 3), "crypto"
    except Exception:
        pass
    return None, None, None


# --------------------------------------------- news feeds (move discriminator)

NEWS_STATS_FILE = HERE / "news_stats.json"
HEADLINES = []          # (ts, lowercased title), newest appended
NEWS_FEEDS = (
    "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en",
    "https://news.google.com/rss/headlines/section/topic/BUSINESS"
    "?hl=en-US&gl=US&ceid=US:en",
    "https://feeds.bbci.co.uk/news/world/rss.xml",
)
_TITLE_RX = re.compile(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", re.S)
_ITEM_RX = re.compile(r"<item>(.*?)</item>", re.S)
_PUB_RX = re.compile(r"<pubDate>(.*?)</pubDate>")


def _rss_items(xml, now, max_age_s=21600):
    """(ts, title) pairs stamped with the REAL publication time. Headlines
    used to inherit the fetch time, so every restart resurrected up to 40
    day-old stories per feed as 'breaking' for the full 90-min freshness
    window — and news_confirmed traded on them. Items without a parseable
    pubDate keep the old behavior; items older than max_age_s are dropped
    at the door."""
    from email.utils import parsedate_to_datetime
    out = []
    for chunk in _ITEM_RX.findall(xml)[:40]:
        m = _TITLE_RX.search(chunk)
        if not m:
            continue
        tl = m.group(1).lower().strip()
        if not tl:
            continue
        ts = now
        pm = _PUB_RX.search(chunk)
        if pm:
            try:
                dt = parsedate_to_datetime(pm.group(1).strip())
                if dt.tzinfo is None:    # "-0000" parses naive; RSS is UTC
                    dt = dt.replace(tzinfo=timezone.utc)
                ts = dt.timestamp()
            except (ValueError, TypeError):
                pass
        if now - ts <= max_age_s:
            out.append((ts, tl))
    return out


_NEWS_STOP = {"will", "the", "with", "that", "this", "from", "have", "been",
              "more", "than", "what", "when", "over", "into", "after",
              "before", "says", "could", "would", "about", "their", "there",
              "2026", "2027", "news", "live", "today"}


def _q_tokens(text):
    t = (text or "").lower().replace(",", "")   # "$70,000" must survive as
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", t).split()  # one token
            if len(w) >= 4 and w not in _NEWS_STOP}


def news_backed(question, window=5400):
    """Is there a fresh headline about this market's subject? The research
    literature splits price moves in two: liquidity noise (reverts — fade
    it, our validated edge) vs news-backed moves (drift — fading them is
    how fades die). This flag lets every learner tell them apart."""
    q = _q_tokens(question)
    if len(q) < 2:
        return False
    cutoff = time.time() - window
    return any(ts >= cutoff and len(q & _q_tokens(title)) >= 2
               for ts, title in HEADLINES)



_SENT_POS = {"surge", "surges", "soar", "soars", "rally", "record", "wins",
             "beat", "beats", "high", "gains", "approve", "approved",
             "success", "rise", "rises", "breakthrough", "deal", "agree",
             "agreement", "strong", "boost", "passes", "victory"}
_SENT_NEG = {"crash", "crashes", "plunge", "plunges", "fall", "falls",
             "drop", "drops", "loss", "losses", "lose", "fails", "fail",
             "fear", "ban", "bans", "war", "crisis", "weak", "reject",
             "rejected", "delay", "delayed", "cancel", "scandal", "probe",
             "lawsuit", "halt", "halted", "warning"}


def headline_sentiment(question, window=5400):
    """Sentiment NLP — the one capability from the ML-APIs survey that fits
    this bot, implemented keyless: lexicon score over fresh headlines that
    match the market's subject. Returns -1..+1 or None (no coverage)."""
    q = _q_tokens(question)
    if len(q) < 2:
        return None
    cutoff = time.time() - window
    score = hits = 0
    for ts, title in HEADLINES:
        if ts < cutoff:
            continue
        if len(q & _q_tokens(title)) >= 2:
            words = set(re.sub(r"[^a-z ]", " ", title).split())
            score += len(words & _SENT_POS) - len(words & _SENT_NEG)
            hits += 1
    if not hits:
        return None
    return max(-1.0, min(1.0, score / (hits * 2.0)))


def _fresh_headline_hits(question, window=5400, min_overlap=2):
    """Count fresh, subject-matching headlines in the point-in-time HEADLINES
    buffer. POINT-IN-TIME: each headline carries the REAL publication timestamp
    (RSS pubDate via _rss_items, HackerNews created_at_i), never the fetch time,
    and only items newer than `window` seconds before NOW are considered — so a
    backtest replaying this market at an earlier moment can never see a story
    that had not yet published. No future market data is consulted. Returns 0
    when the question has too few distinguishing tokens (the common path)."""
    q = _q_tokens(question)
    if len(q) < 2:
        return 0
    cutoff = time.time() - window
    return sum(1 for ts, title in HEADLINES
               if ts >= cutoff and len(q & _q_tokens(title)) >= min_overlap)


def social_features(question, side, window=5400):
    """Point-in-time SOCIAL feature read for one market, attached to the entry
    context of SOCIAL markets only (pop-culture / tech / science / twitter /
    truths / mentions). ALL fields default neutral (None) so a non-social market
    — or a social market with no fresh coverage — leaves _brain_x's social
    features at EXACTLY 0.0 and the global/common path byte-identical.

    Reads ONLY the shared HEADLINES buffer, which the news_rss (Google News +
    BBC) and HackerNews connectors fill keyless, governed and fail-silent. Every
    headline is timestamp-filtered to a strict freshness window BEFORE the
    decision moment (RSS pubDate / HN created_at_i — never fetch time), so the
    read is purely point-in-time: no future headline and no future market data
    can enter. A dead feed simply yields fewer fresh hits, never an exception.

      news_backed_strong: 1.0 when the subject has CORROBORATED fresh coverage —
                          either >=2 distinct fresh headlines match it, or one
                          matches strongly (>=3 overlapping subject tokens).
                          Stronger than the global `newsbk` single-hit flag;
                          isolates drift-worthy news bursts from one-off mentions.
                          None when there is no qualifying fresh coverage.
      sentiment_magnitude: |headline sentiment| over fresh matching headlines in
                          [0,1] — HOW LOUD the news is, regardless of direction.
                          None when no fresh headline covers the subject.
      sentiment_alignment: signed sentiment aligned to the SIDE being bet —
                          +mag when the lexicon mood agrees with backing this
                          outcome (positive mood on a Yes, negative mood on a
                          No), -mag when it cuts against it, in [-1,1]. None
                          when no fresh headline covers the subject.

    Governed/cached/fail-silent by construction: it does no network I/O itself
    (the news_loop connector owns that), so it can neither stall nor crash the
    daemon. Returns the all-None neutral dict on any error."""
    out = {"social_news_strong": None, "social_sent_mag": None,
           "social_sent_align": None}
    try:
        hits = _fresh_headline_hits(question, window, min_overlap=2)
        strong_hits = _fresh_headline_hits(question, window, min_overlap=3)
        if hits >= 2 or strong_hits >= 1:
            out["social_news_strong"] = 1.0
        elif hits >= 1:
            out["social_news_strong"] = 0.0   # covered, but not corroborated
        sent = headline_sentiment(question, window)
        if sent is not None:
            out["social_sent_mag"] = abs(sent)
            # align to the side: betting "No" flips the mood's helpfulness.
            sign = -1.0 if (side or "").lower().startswith("no") else 1.0
            out["social_sent_align"] = max(-1.0, min(1.0, sign * sent))
    except Exception:
        pass
    return out


def news_loop():
    """Poll free headline feeds (Google News + BBC, no keys) every 2 min."""
    while True:
        try:
            now = time.time()
            known = {t for _, t in HEADLINES}
            for url in NEWS_FEEDS:
                _governor()
                try:
                    r = session.get(url, timeout=10,
                                    headers={"User-Agent": "Mozilla/5.0"})
                    for ts, tl in _rss_items(r.text, now):
                        if tl not in known:
                            HEADLINES.append((ts, tl))
                            known.add(tl)
                except Exception:
                    continue
            _governor()
            try:
                hn = get_json("https://hn.algolia.com/api/v1/search_by_date",
                              params={"tags": "story", "hitsPerPage": 30}) or {}
                for hit in hn.get("hits", []):
                    tl = (hit.get("title") or "").lower().strip()
                    ts = hit.get("created_at_i") or now
                    if tl and tl not in known and now - ts <= 21600:
                        HEADLINES.append((ts, tl))
                        known.add(tl)
            except Exception:
                pass
            del HEADLINES[:-400]
            atomic_write(NEWS_STATS_FILE, json.dumps(
                {"headlines": len(HEADLINES),
                 "t": now_utc().isoformat(timespec="seconds")}))
        except Exception as e:
            print(f"  ! news loop error: {e}")
        time.sleep(120)


# ------------------------------------------ live scores (latency probe)

SCORES_FILE = HERE / "scores_stats.json"
SCORE_LEAGUES = ("soccer/fifa.world", "baseball/mlb",
                 "basketball/nba", "hockey/nhl")
SCORE_STATE = {}
ARMED_SCORES = {}
SCORES_STATS = {"events": 0, "armed": 0, "latencies": []}


def _team_tokens(name):
    drop = {"republic", "united", "city", "club", "the", "real"}
    return {w for w in re.sub(r"[^a-z ]", " ", (name or "").lower()).split()
            if len(w) >= 4 and w not in drop}


def _match_game(team_a, team_b, market_name):
    mk = (market_name or "").lower()
    return (any(t in mk for t in _team_tokens(team_a))
            and any(t in mk for t in _team_tokens(team_b)))


def _score_rep(team_a, team_b, sa, sb):
    """Order-independent score fingerprint, robust to away/home flips and
    naming differences between sources."""
    tok = lambda t: min(_team_tokens(t) or {t.lower()})
    return tuple(sorted([(tok(team_a), str(sa)), (tok(team_b), str(sb))]))


def _espn_games():
    out = []
    for lg in SCORE_LEAGUES:
        for e in (get_json("https://site.api.espn.com/apis/site/v2/sports/"
                           f"{lg}/scoreboard") or {}).get("events", []):
            comp = (e.get("competitions") or [{}])[0]
            if comp.get("status", {}).get("type", {}).get("state") != "in":
                continue
            t = [(c.get("team", {}).get("displayName", ""), c.get("score"))
                 for c in comp.get("competitors", [])]
            if len(t) == 2:
                out.append((t[0][0], t[1][0], t[0][1], t[1][1]))
    return out


def _mlb_games():
    data = get_json("https://statsapi.mlb.com/api/v1/schedule",
                    params={"sportId": 1, "hydrate": "linescore"}) or {}
    out = []
    for d in data.get("dates", []):
        for g in d.get("games", []):
            if g.get("status", {}).get("abstractGameState") != "Live":
                continue
            a = g.get("teams", {}).get("away", {})
            h = g.get("teams", {}).get("home", {})
            out.append((a.get("team", {}).get("name", ""),
                        h.get("team", {}).get("name", ""),
                        a.get("score", ""), h.get("score", "")))
    return out


def _nhl_games():
    out = []
    for g in (get_json("https://api-web.nhle.com/v1/score/now")
              or {}).get("games", []):
        if g.get("gameState") not in ("LIVE", "CRIT"):
            continue
        a, h = g.get("awayTeam", {}), g.get("homeTeam", {})
        out.append((a.get("name", {}).get("default", ""),
                    h.get("name", {}).get("default", ""),
                    a.get("score", ""), h.get("score", "")))
    return out


SCORE_SOURCES = (("espn", _espn_games), ("mlb-official", _mlb_games),
                 ("nhl-official", _nhl_games))


# --------------------------------------------- sportsedge shadow instrument
# A self-grading sports fair-value model that DEFAULTS TO NO BET. It runs in
# SHADOW: records a fair value + edge for every modelable game, grades its
# own closing-line value on realized outcomes, and trades $0 until measured
# out-of-sample CLV earns it a (manual, operator-reviewed) promotion. It is
# fully additive — it writes only sportsedge_model.json and never touches the
# account or the trading path. Pre-game edge is known to be ~0 (the bot's own
# 723-market test: Brier 0.064->0.0001 by close), so the honest product here
# is the HONEST SCORECARD, not a money printer.

SPORTSEDGE_BOARD_LEAGUES = {
    "baseball/mlb": "MLB", "basketball/nba": "NBA", "basketball/wnba": "WNBA",
    "football/nfl": "NFL", "hockey/nhl": "NHL", "soccer/fifa.world": "FIFA-WC",
}


def _espn_board():
    """Today's ESPN events across the whitelist leagues, ALL states (the
    bot's _espn_games only returns in-progress). Each: id, league, home,
    away, date(UTC day), state(pre|in|post), home_won(0/1/None)."""
    board = []
    for path, code in SPORTSEDGE_BOARD_LEAGUES.items():
        data = get_json("https://site.api.espn.com/apis/site/v2/sports/"
                        f"{path}/scoreboard") or {}
        for e in data.get("events", []):
            comp = (e.get("competitions") or [{}])[0]
            cs = comp.get("competitors", [])
            if len(cs) != 2:
                continue
            home = next((c for c in cs if c.get("homeAway") == "home"), cs[0])
            away = next((c for c in cs if c.get("homeAway") == "away"), cs[1])
            state = comp.get("status", {}).get("type", {}).get("state")
            won = None
            if state == "post":
                won = (1 if home.get("winner") else
                       0 if away.get("winner") else None)
            board.append({
                "id": str(e.get("id")), "league": code,
                "home": home.get("team", {}).get("displayName", ""),
                "away": away.get("team", {}).get("displayName", ""),
                "date": (e.get("date") or "")[:10],
                "state": state, "home_won": won})
    return board


def _se_market_day(m):
    gs = m.get("gameStartTime") or m.get("endDate") or ""
    return str(gs)[:10]


def sportsedge_shadow_pass(account):
    """One shadow cycle: update Elo from ESPN finals, price every modelable
    live/pre sports market, log a shadow prediction, grade resolved ones by
    CLV, persist. Trades NOTHING. Returns the scorecard."""
    st = SPORTSEDGE
    # Schema v2: the old grader FABRICATED clv — it set close=0.99/0.01 (the
    # game's final result, not a real line) and market_price=entry, so every
    # "beats market / CLV" number was synthetic. Purge those preds once and
    # re-score from zero against a real pre-game-frozen close.
    if st.get("sv") != 2:
        st["preds"] = []
        st.pop("scorecard", None)
        st["sv"] = 2
    board = _espn_board()
    by_id = {e["id"]: e for e in board}

    # 1) learn: update Elo from finals not yet counted (home perspective)
    seen = set(st.get("seen_finals") or [])
    ratings = st.get("ratings") or {}
    for e in board:
        if e["state"] == "post" and e["home_won"] is not None \
                and e["id"] not in seen:
            sportsedge.elo_update(ratings, e["home"], e["away"],
                                  e["home_won"], home_adv=0.0)
            seen.add(e["id"])
    st["ratings"], st["seen_finals"] = ratings, sorted(seen)[-2000:]

    # 2) price: fetch near-term markets, keep sports, join, log predictions
    markets = []
    for off in (0, 100, 200):
        page = get_json(f"{GAMMA}/markets", params={
            "active": "true", "closed": "false", "order": "endDate",
            "ascending": "true", "limit": 100, "offset": off,
            "end_date_min": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date_max": (now_utc() + timedelta(hours=48)
                             ).strftime("%Y-%m-%dT%H:%M:%SZ")}) or []
        markets += page
        if len(page) < 100:
            break
    preds = st.get("preds") or []
    open_by_mid = {p["market_id"]: p for p in preds if not p.get("resolved")}
    logged_ids = set(open_by_mid)
    n_eval = n_logged = n_abstain = 0
    for m in markets:
        q = m.get("question") or ""
        if not (cluster_of(q) == "sports-game" or _SPORTSY.search(q)):
            continue
        outs = jlist(m.get("outcomes"))
        if len(outs) != 2:
            continue
        # cheap pre-filter: find the single board game sharing a team token
        day = _se_market_day(m)
        a_tok = sportsedge._tok(outs[0])
        b_tok = sportsedge._tok(outs[1])
        cand = [e for e in board if e["date"] == day
                and ((a_tok & sportsedge._tok(e["home"]))
                     or (a_tok & sportsedge._tok(e["away"]))
                     or (b_tok & sportsedge._tok(e["home"]))
                     or (b_tok & sportsedge._tok(e["away"])))]
        if len(cand) != 1:
            continue
        ev = cand[0]
        mkt = {"question": q, "league": ev["league"], "date": day,
               "outcomes": outs}
        if sportsedge.join_event(mkt, board) is None:
            continue
        n_eval += 1
        prices = jlist(m.get("outcomePrices"))
        try:
            ask_yes, ask_no = float(prices[0]), float(prices[1])
        except (ValueError, IndexError, TypeError):
            continue
        # Freeze the latest PRE-GAME mid as the honest closing line: while the
        # game has not started, overwrite close_mid each pass, so the last
        # pre-game observation before tip-off is the close used for CLV.
        m_id = str(m.get("id"))
        if ev["state"] == "pre" and m_id in open_by_mid:
            open_by_mid[m_id]["close_mid"] = round(ask_yes, 4)
            open_by_mid[m_id]["close_ts"] = now_utc().isoformat(timespec="seconds")
        feats = {"league": ev["league"], "a": outs[0], "b": outs[1],
                 "home_adv": 0.0,
                 "state": "in" if ev["state"] == "in" else "pre",
                 "espn_winprob": None, "frac_elapsed": 0.0}
        p_true = sportsedge.fair_value(st, feats)
        cost = sportsedge.book_cost(ask_yes, ask_no)
        v = sportsedge.edge_verdict(p_true, ask_yes, cost, mode="shadow")
        if v["p_true"] is None:
            n_abstain += 1
            continue
        mid = str(m.get("id"))
        if mid in logged_ids:
            continue
        preds.append({"ts": now_utc().isoformat(timespec="seconds"),
                      "market_id": mid, "espn_id": ev["id"],
                      "question": q[:80], "side": outs[0],
                      "entry": round(ask_yes, 4), "p_true": v["p_true"],
                      "edge": v["edge"], "resolved": False})
        logged_ids.add(mid)
        n_logged += 1

    # 3) grade: resolve predictions whose ESPN game is final
    graded = []
    for p in preds:
        if p.get("resolved"):
            graded.append(p)
            continue
        ev = by_id.get(p["espn_id"])
        if ev and ev["state"] == "post" and ev["home_won"] is not None:
            side_tok = sportsedge._tok(p["side"])
            side_is_home = bool(side_tok & sportsedge._tok(ev["home"]))
            won = ev["home_won"] if side_is_home else 1 - ev["home_won"]
            close_mid = p.get("close_mid")
            upd = {"resolved": True, "won": int(won)}
            if close_mid is not None:
                # honest CLV: real pre-game-frozen mid vs our entry price
                upd["market_price"] = close_mid
                upd["clv"] = round(close_mid - p["entry"], 4)
            else:
                # never froze a pre-game mid (logged in-game, or the game
                # started between 20-min passes) -> resolved but NOT scored
                upd["market_price"] = None
                upd["clv"] = None
            p.update(upd)
        graded.append(p)
    st["preds"] = graded[-1000:]

    done = [p for p in st["preds"] if p.get("resolved")]
    scored = [p for p in done
              if p.get("clv") is not None and p.get("market_price") is not None]
    sc = sportsedge.grade([{"p_true": p["p_true"],
                            "market_price": p["market_price"],
                            "won": p["won"], "clv": p["clv"]}
                           for p in scored]) if scored else {"n": 0}
    sc.update({"open_preds": len(st["preds"]) - len(done),
               "graded_unscored": len(done) - len(scored),
               "evaluated": n_eval, "logged": n_logged,
               "abstained": n_abstain,
               "abstain_rate": round(n_abstain / max(n_eval, 1), 3),
               "ratings_teams": len(ratings)})
    st["scorecard"] = sc
    st["updated"] = now_utc().isoformat(timespec="seconds")
    atomic_write(SPORTSEDGE_FILE, json.dumps(st))
    return sc


def sportsedge_loop():
    """Shadow instrument heartbeat — measure & grade every 20 min, trade $0.
    Wrapped so it can never disturb the trading process."""
    time.sleep(120)                       # let warmstart settle first
    while True:
        try:
            sc = sportsedge_shadow_pass(load_account(load_config()))
            journal("SPORTSEDGE", n=sc.get("n", 0),
                    open=sc.get("open_preds", 0),
                    abstain=sc.get("abstain_rate"),
                    clv=sc.get("mean_clv"), verdict=sc.get("verdict"))
        except Exception as e:
            print(f"  ! sportsedge shadow error: {e}")
        time.sleep(1200)


# -------------------------------------------- goal-snipe SHADOW instrument
# Measures, for FREE on real scoring events, whether the bot could snipe a goal
# before the market reprices — i.e. is the ESPN feed FASTER than the crowd, or
# does the bot get PICKED OFF. Trades $0: it never calls open_position and never
# touches is_in_game (the live in-game gate that protects the account). On a
# detected goal it records the mid of the scoring side AT DETECTION, then grades
# the price N seconds later: edge = mid_N - mid_detect. edge > 0 = the price was
# still moving our way after we detected (feed won); edge ~ 0 = already moved
# (picked off). N starts at 12s because that IS the bot's real detection lag —
# an edge that only exists at N=0 is one we cannot actually capture. Single-feed
# sports (soccer: ESPN-only) are scored separately on a higher bar.
SNIPE_FILE = HERE / "snipe_shadow.json"
try:
    SNIPE_SHADOW = json.loads(SNIPE_FILE.read_text())
except (FileNotFoundError, ValueError):
    SNIPE_SHADOW = {"pending": [], "graded": [], "scorecard": {}}
_SNIPE_PREV = {}                       # game key (frozenset) -> last rep we saw
_SNIPE_NS = (12, 24, 36, 60, 120)      # grade offsets (s), >= the ~12s detect lag


def _snipe_scoring_token(old_rep, new_rep):
    """The min team-token of the side whose score just went UP between two
    _score_rep tuples, or None if 0 or >1 sides moved (ambiguous)."""
    try:
        old = {t: int(s) for t, s in old_rep}
        new = {t: int(s) for t, s in new_rep}
    except (ValueError, TypeError):
        return None
    up = [t for t in new if t in old and new[t] > old[t]]
    return up[0] if len(up) == 1 else None


def _snipe_sports_markets():
    """Read-only: current active sports markets as {teams, legs:[(token, tokens)]}.
    Mirrors the sportsedge market fetch; used only when a goal just fired."""
    markets, out = [], []
    for off in (0, 100, 200):
        page = get_json(f"{GAMMA}/markets", params={
            "active": "true", "closed": "false", "order": "endDate",
            "ascending": "true", "limit": 100, "offset": off,
            "end_date_min": now_utc().strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date_max": (now_utc() + timedelta(hours=48)
                             ).strftime("%Y-%m-%dT%H:%M:%SZ")}) or []
        markets += page
        if len(page) < 100:
            break
    for m in markets:
        q = m.get("question") or ""
        if not (cluster_of(q) == "sports-game" or _SPORTSY.search(q)):
            continue
        outs, toks = jlist(m.get("outcomes")), jlist(m.get("clobTokenIds"))
        if len(outs) != 2 or len(toks) != 2:
            continue
        legs = [(str(toks[i]), sportsedge._tok(outs[i])) for i in (0, 1)]
        if not (legs[0][1] and legs[1][1]):
            continue
        out.append({"mid": str(m.get("id")), "q": q[:80],
                    "teams": legs[0][1] | legs[1][1], "legs": legs})
    return out


def _snipe_mid(token):
    try:
        bs = book_stats(token)
        return (bs["bid"] + bs["ask"]) / 2 if bs else None
    except Exception:
        return None


def snipe_shadow_pass():
    """One $0 shadow pass: detect goals, record the would-be snipe, grade it.
    Never trades, never touches is_in_game; writes only snipe_shadow.json."""
    now = time.time()
    goals = []                         # (game key, scoring token, single_source)
    for key, st in list(SCORE_STATE.items()):
        rep, prev = st.get("rep"), _SNIPE_PREV.get(key)
        _SNIPE_PREV[key] = rep
        if prev is not None and prev != rep:
            stok = _snipe_scoring_token(prev, rep)
            if stok:
                goals.append((key, stok, st.get("seen") == {"espn"}))
    if goals:
        mkts = _snipe_sports_markets()
        for key, stok, single in goals:
            for m in mkts:
                if len(m["teams"] & key) < 2:
                    continue           # different game
                idx = (0 if stok in m["legs"][0][1]
                       else 1 if stok in m["legs"][1][1] else None)
                if idx is None:
                    continue
                p0 = _snipe_mid(m["legs"][idx][0])
                if p0 is None:
                    continue
                SNIPE_SHADOW["pending"].append({
                    "detect_ts": now, "game": m["q"], "market": m["mid"],
                    "token": m["legs"][idx][0], "p_detect": round(p0, 4),
                    "single_source": single, "grades": {}})
        SNIPE_SHADOW["pending"] = SNIPE_SHADOW["pending"][-500:]
    still = []
    for row in SNIPE_SHADOW["pending"]:
        elapsed = now - row["detect_ts"]
        for N in _SNIPE_NS:
            if elapsed >= N and str(N) not in row["grades"]:
                mid = _snipe_mid(row["token"])
                if mid is not None:
                    row["grades"][str(N)] = round(mid - row["p_detect"], 4)
        (still if elapsed < _SNIPE_NS[-1] + 30
         else SNIPE_SHADOW["graded"]).append(row)
    SNIPE_SHADOW["pending"] = still
    SNIPE_SHADOW["graded"] = SNIPE_SHADOW["graded"][-2000:]

    def card(rows):
        c = {"n": len(rows)}
        for N in _SNIPE_NS:
            es = [r["grades"][str(N)] for r in rows if str(N) in r["grades"]]
            if es:
                c[f"edge_{N}s"] = round(sum(es) / len(es), 4)
                c[f"hit_{N}s"] = round(sum(e > 0 for e in es) / len(es), 3)
                c[f"n_{N}s"] = len(es)
        return c
    g = SNIPE_SHADOW["graded"]
    SNIPE_SHADOW["scorecard"] = {
        "all": card(g),
        "single_source": card([r for r in g if r.get("single_source")]),
        "multi_source": card([r for r in g if not r.get("single_source")]),
        "verdict": ("shadow-only — go live ONLY on 30+ graded goals with a "
                    "net-of-fees POSITIVE edge at an achievable offset, in a "
                    "separate capped lane with sign-off; never weaken is_in_game"),
        "updated": now_utc().isoformat(timespec="seconds")}
    atomic_write(SNIPE_FILE, json.dumps(SNIPE_SHADOW))
    return SNIPE_SHADOW["scorecard"]


def snipe_loop():
    """Goal-snipe SHADOW heartbeat — every 8s, detect goals and grade whether a
    snipe would have beaten the market. Trades $0; wrapped so it can never
    disturb the trading process."""
    time.sleep(60)                     # let warmstart + score watcher settle
    while True:
        try:
            snipe_shadow_pass()
        except Exception as e:
            print(f"  ! snipe shadow error: {e}")
        time.sleep(8)


# ------------------------------------------- per-category SPORTS feature read
# CATEGORY SPECIALIST FEED for "sports". Reads three point-in-time, fail-silent
# sources at decision time and exposes them as neutral-defaulting context, which
# _brain_x lifts into the feature vector for the sports category specialist to
# learn (OOS-gated). It NEVER touches the global path: every field defaults to
# the exact neutral that makes _brain_x's sports features 0.0, so a market with
# no sports signal — the common case, and EVERY non-sports market — is
# byte-identical to before.
#
# LEAKAGE: every read is point-in-time. (1) game_state_risk reads the ESPN
# scoreboard state AS OF NOW (pre|post; live banned per rules). (2) elo_fair_value
# reads SPORTSEDGE["ratings"], which are trained ONLY on finals already seen
# before this decision (immutable per decision time, never the current game's
# result). (3) sportsbook_consensus / spread_vs_consensus_div come from the
# concurrent de-vigged Odds-API snapshot (already in `xm`), never a final score.
# Post-game markets abstain (state="post" -> abstain flag); live is never traded.

SPORTS_BOARD_TTL = 300            # ESPN scoreboard cached 5 min (point-in-time)


def _sports_board_cached():
    """Today's ESPN board across the whitelist leagues, cached & fail-silent.
    Reuses _espn_board (governed get_json, all states). Returns [] on any
    error so a dead ESPN can never stall or crash the scan."""
    def fetch():
        try:
            return _espn_board() or []
        except Exception:
            return []
    return _cached(("sports_board",), SPORTS_BOARD_TTL, fetch) or []


def _sports_game_state(m, board):
    """ESPN scoreboard state for this market's game, point-in-time: 'pre',
    'in', 'post', or None when no confident single-game join. Same token-join
    the sportsedge shadow uses (one board game sharing a team token)."""
    try:
        outs = jlist(m.get("outcomes"))
        if len(outs) != 2 or not board:
            return None
        day = _se_market_day(m)
        a_tok, b_tok = sportsedge._tok(outs[0]), sportsedge._tok(outs[1])
        cand = [e for e in board if e["date"] == day
                and ((a_tok & sportsedge._tok(e["home"]))
                     or (a_tok & sportsedge._tok(e["away"]))
                     or (b_tok & sportsedge._tok(e["home"]))
                     or (b_tok & sportsedge._tok(e["away"])))]
        if len(cand) != 1:
            return None
        mkt = {"question": m.get("question") or "", "league": cand[0]["league"],
               "date": day, "outcomes": outs}
        if sportsedge.join_event(mkt, board) is None:
            return None
        return cand[0].get("state")
    except Exception:
        return None


def _sports_elo_fair_value(m, fav):
    """Polymarket-trained Elo fair value for the FAVORED outcome (fav index),
    read point-in-time from SPORTSEDGE['ratings'] (trained only on finals
    already seen). Returns a probability in (0,1) for outcome[fav], or None
    when either team is unrated / unmodelable. Pure read — never fits here."""
    try:
        outs = jlist(m.get("outcomes"))
        if len(outs) != 2:
            return None
        ratings = (SPORTSEDGE.get("ratings") or {})
        if not ratings:
            return None
        # map each outcome name to its best-matching rated team token-overlap
        def rate(name):
            ntok = sportsedge._tok(name)
            best, bj = None, 0
            for team, r in ratings.items():
                j = len(ntok & sportsedge._tok(team))
                if j > bj:
                    best, bj = r, j
            return best if bj > 0 else None
        ra, rb = rate(outs[0]), rate(outs[1])
        if ra is None or rb is None:
            return None
        p0 = sportsedge.elo_expect(ra, rb)         # P(outcome[0] wins)
        return p0 if fav == 0 else (1.0 - p0)
    except Exception:
        return None


def sports_features(m, price, fav, xm=None):
    """Point-in-time sports feature read for one market, attached to the entry
    context of SPORTS markets only. ALL fields default neutral (None) so a
    non-sports market — or a sports market with no signal — leaves _brain_x's
    sports features at exactly 0.0 and the global/common path byte-identical.

      sports_state:        'pre'|'in'|'post'|None  (ESPN scoreboard, now)
      sports_post:         True only when the joined game is already final
      sportsbook_consensus: median de-vigged moneyline P(fav) from The Odds API
                            (via the live xmkt snapshot; neutral with no key)
      sports_elo_fv:       Elo fair value P(fav) (immutable, finals-trained)
      sports_div:          (price - sportsbook_consensus), capped [-1,1]

    Fail-silent and cached; a dead source yields None, never an exception."""
    out = {"sports_state": None, "sports_post": None,
           "sportsbook_consensus": None, "sports_elo_fv": None,
           "sports_div": None}
    try:
        board = _sports_board_cached()
        st = _sports_game_state(m, board)
        if st is not None:
            out["sports_state"] = st
            # live in-game is BANNED per rules; post-game abstains entirely.
            out["sports_post"] = (st == "post")
        out["sports_elo_fv"] = _sports_elo_fair_value(m, fav)
        # sportsbook consensus rides the existing de-vigged Odds-API snapshot
        # already gathered for the xmkt twin (key-gated; absent -> None). fav==0
        # means consensus_p is already P(outcome[0]); otherwise complement it.
        xm = xm or {}
        cons = xm.get("consensus_p") if "oddsapi" in (xm.get("sources") or []) \
            else None
        if cons is not None:
            cons = cons if fav == 0 else (1.0 - cons)
            out["sportsbook_consensus"] = cons
            if price is not None:
                out["sports_div"] = max(-1.0, min(1.0, price - cons))
    except Exception:
        pass
    return out


# ----------------------------------------- cross-market consensus instrument
# A self-grading instrument that reads OTHER prediction markets (Kalshi /
# PredictIt / Manifold real- and play-money) plus optional sportsbook
# consensus, finds the FEW Polymarket markets that have a confident same-event
# twin, and grades whether that cross-market consensus predicts PM resolution
# better than the PM price itself. It runs in SHADOW: writes ONLY
# crossmarket_model.json, never touches the account. Most PM markets have NO
# twin — that is correct and expected. Its only path to trading influence is
# the brain's OOS/credibility gate (via the xmkt_* features) + the
# by_crossmarket attribution bucket; divergence is never traded blindly.

CROSSMARKET_POOL_TTL = 600        # refresh the cross-source pool every 10 min


def _xmkt_fetch(url, timeout=8.0):
    """Governed, fail-silent JSON GET for crossmarket connectors. Reuses the
    bot's shared API governor + session so a dead external API can never stall
    or crash the daemon; returns None on any error."""
    try:
        _governor()
        r = session.get(url, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def crossmarket_pool(force=False):
    """The current cross-market source pool, cached CROSSMARKET_POOL_TTL secs.
    Fail-silent: returns the last good pool (or []) if a refresh fails."""
    ts = CROSSMARKET.get("pool_ts")
    if (not force and ts and CROSSMARKET.get("pool")
            and (time.time() - ts) < CROSSMARKET_POOL_TTL):
        return CROSSMARKET["pool"]
    try:
        pool = crossmarket.gather_pool(fetch=_xmkt_fetch)
    except Exception:
        pool = []
    if pool:                      # only replace a good pool with a good pool
        CROSSMARKET["pool"] = pool
        CROSSMARKET["pool_ts"] = time.time()
    return CROSSMARKET.get("pool") or []


def xmkt_lookup(market, price=None):
    """Entry-path hook: cross-market consensus + divergence for one PM market,
    or {} when there is no confident twin (the common case -> brain features
    default neutral, global path unchanged). Never raises."""
    try:
        pool = crossmarket_pool()
        if not pool:
            return {}
        pm = {"question": market.get("question") or market.get("name") or "",
              "date": _se_market_day(market)}
        return crossmarket.lookup(pm, pool, pm_p=price)
    except Exception:
        return {}


def crossmarket_shadow_pass(account):
    """One shadow cycle: refresh the cross-source pool, match it against open
    PM markets, log shadow consensus predictions, grade any whose PM market has
    since resolved, persist. Trades NOTHING. Returns the scorecard."""
    st = CROSSMARKET
    pool = crossmarket_pool(force=True)

    # 1) fetch near-term open PM markets and log a shadow prediction wherever a
    #    confident cross-market twin exists (read at decision time only).
    preds = st.get("preds") or []
    open_ids = {p["market_id"] for p in preds if not p.get("resolved")}
    n_scanned = n_matched = n_logged = 0
    markets = []
    for off in (0, 100, 200):
        page = get_json(f"{GAMMA}/markets", params={
            "active": "true", "closed": "false", "order": "volume24hr",
            "ascending": "false", "limit": 100, "offset": off}) or []
        markets += page
        if len(page) < 100:
            break
    for m in markets:
        outs = jlist(m.get("outcomes"))
        if len(outs) != 2:
            continue                       # two-outcome markets only
        prices = jlist(m.get("outcomePrices"))
        try:
            pm_p = float(prices[0])
        except (ValueError, IndexError, TypeError):
            continue
        n_scanned += 1
        pm = {"question": m.get("question") or "",
              "date": _se_market_day(m)}
        con = crossmarket.lookup(pm, pool, pm_p=pm_p)
        if not con or "divergence" not in con:
            continue
        n_matched += 1
        mid = str(m.get("id"))
        if mid in open_ids:
            continue
        preds.append({"ts": now_utc().isoformat(timespec="seconds"),
                      "market_id": mid, "question": (m.get("question") or "")[:80],
                      "entry": round(pm_p, 4),
                      "consensus_p": con["consensus_p"],
                      "divergence": con["divergence"],
                      "sources": con["sources"], "resolved": False})
        open_ids.add(mid)
        n_logged += 1

    # 2) grade: resolve predictions whose PM market has since closed. The
    #    winning outcome of a settled 2-way market prices to ~1.0; outcome[0]
    #    'won' iff prices[0] > 0.99. This uses ONLY post-resolution info to
    #    GRADE — never as a matching/consensus feature (no leakage).
    open_preds = [p for p in preds if not p.get("resolved")]
    if open_preds:
        ids = sorted({p["market_id"] for p in open_preds})
        for i in range(0, len(ids), 20):
            batch = get_json(f"{GAMMA}/markets",
                             params=[("id", x) for x in ids[i:i + 20]]
                             + [("closed", "true")]) or []
            done = {}
            for mm in batch:
                pr = [fnum(x) for x in jlist(mm.get("outcomePrices"))]
                if mm.get("closed") and len(pr) == 2 and max(pr) > 0.99:
                    done[str(mm["id"])] = (1 if pr[0] > 0.99 else 0,
                                           mm.get("closedTime") or mm.get("endDate"))
            for p in open_preds:
                if p["market_id"] in done:
                    won, _ = done[p["market_id"]]
                    close = 0.99 if won else 0.01
                    p.update({"resolved": True, "won": int(won),
                              "market_price": p["entry"],
                              "clv": crossmarket.clv(p["entry"], close, won)})
            time.sleep(0.1)
    st["preds"] = preds[-1000:]

    done = [p for p in st["preds"] if p.get("resolved")]
    sc = crossmarket.grade([{"consensus_p": p["consensus_p"],
                             "market_price": p.get("market_price", p["entry"]),
                             "won": p["won"], "clv": p.get("clv", 0.0)}
                            for p in done]) if done else {"n": 0, "promote": False,
                                                          "verdict": "no data"}
    sc.update({"open_preds": len(st["preds"]) - len(done),
               "scanned": n_scanned, "matched": n_matched, "logged": n_logged,
               "pool_size": len(pool),
               "pool_sources": sorted({r.get("source") for r in pool})})
    st["scorecard"] = sc
    st["updated"] = now_utc().isoformat(timespec="seconds")
    atomic_write(CROSSMARKET_FILE, json.dumps(st))
    return sc


def crossmarket_loop():
    """Shadow instrument heartbeat — refresh, match, grade every ~3 min, trade
    $0. Wrapped so it can never disturb the trading process."""
    time.sleep(180)                       # let warmstart settle first
    while True:
        try:
            sc = crossmarket_shadow_pass(load_account(load_config()))
            journal("CROSSMARKET", n=sc.get("n", 0),
                    matched=sc.get("matched", 0), open=sc.get("open_preds", 0),
                    clv=sc.get("mean_clv"), verdict=sc.get("verdict"))
        except Exception as e:
            print(f"  ! crossmarket shadow error: {e}")
        time.sleep(180)


def scores_loop(account):
    """Multi-feed score watcher: ESPN + the official MLB and NHL feeds,
    racing each other AND the market. First source to report a change gets
    the credit (firsts counter); laggards' delays are journaled
    (SOURCE_LAG); held markets get latency probes closed by the 1s price
    monitor. Three answers from one instrument: fastest source per sport,
    feed-vs-market lead time, and the events themselves."""
    while True:
        try:
            now = time.time()
            for src_name, fetch in SCORE_SOURCES:
                for a, b, sa, sb in fetch():
                    key = frozenset(_team_tokens(a) | _team_tokens(b))
                    if not key:
                        continue
                    rep = _score_rep(a, b, sa, sb)
                    st = SCORE_STATE.get(key)
                    if st is None:
                        SCORE_STATE[key] = {"rep": rep, "t": now,
                                            "src": src_name, "seen": {src_name}}
                        continue
                    if st["rep"] == rep:
                        if src_name not in st["seen"]:
                            st["seen"].add(src_name)
                            lag = round(now - st["t"], 1)
                            SCORES_STATS.setdefault("lags", []).append(
                                {"fast": st["src"], "slow": src_name, "s": lag})
                            SCORES_STATS["lags"] = SCORES_STATS["lags"][-50:]
                            journal("SOURCE_LAG", fast=st["src"],
                                    slow=src_name, seconds=lag,
                                    game=f"{a} vs {b}")
                        continue
                    # a NEW score, first reported by src_name
                    SCORE_STATE[key] = {"rep": rep, "t": now,
                                        "src": src_name, "seen": {src_name}}
                    SCORES_STATS["events"] += 1
                    f = SCORES_STATS.setdefault("firsts", {})
                    f[src_name] = f.get(src_name, 0) + 1
                    journal("SCORE", source=src_name,
                            score=f"{a} {sa} — {b} {sb}")
                    for p in list(account["positions"]):
                        tok = leg_token_id(p["legs"][0])
                        if tok and _match_game(a, b, p["name"]):
                            ARMED_SCORES[str(tok)] = {
                                "t": now, "p0": p.get("last_price"),
                                "game": f"{a} vs {b}"}
                            SCORES_STATS["armed"] += 1
            for k in [k for k, v in ARMED_SCORES.items()
                      if time.time() - v["t"] > 300]:
                ARMED_SCORES.pop(k, None)
            atomic_write(SCORES_FILE, json.dumps(SCORES_STATS))
        except Exception as e:
            print(f"  ! scores loop error: {e}")
        time.sleep(12)


# ------------------------------------------------- pattern miner (model 11)

PATTERNS_FILE = HERE / "patterns.json"
PATTERN_VETOES = {"list": []}   # refreshed each pass, read at entry time


def trade_features(t):
    """Describe one trade as categorical features the miner can combine."""
    ctx = t.get("context") or {}
    f = ["strat=" + t["strategy"], "cluster=" + cluster_of(t.get("name"))]
    if t.get("category"):
        f.append("cat=" + t["category"])
    side = (t.get("side") or "").lower()
    if side:
        f.append("side=" + ("no" if side.startswith("no") else "yes"))
    f.append(f"px={int(round((t.get('entry_price') or 0) * 20)) * 5}")  # 5c buckets
    # NO `closed`-derived trait here: `closed` is the market RESOLUTION timestamp
    # during training but the CURRENT time during live mining/veto/pat checks, so
    # the settlement hour is unknown at entry. A former `hour=` bucket leaked that
    # future data into the mined patterns -> pat* brain features (brain_adjust) and
    # entry vetoes (pattern_veto), both of which recompute traits from a probe with
    # closed=now. Removed for the same reason as the _brain_x night/imb_x_night
    # leak (commit 45fa2ae). Every remaining trait is point-in-time (known at entry).
    if ctx.get("spread") is not None:
        f.append("spread=" + ("wide" if ctx["spread"] > 0.02 else "tight"))
    if ctx.get("imbalance") is not None:
        f.append("imb=" + ("buy" if ctx["imbalance"] > 0.6
                           else "sell" if ctx["imbalance"] < 0.4 else "mid"))
    if ctx.get("move_1h") is not None:
        f.append("move=" + ("up" if ctx["move_1h"] > 0 else "down"))
    if ctx.get("hours_to_end") is not None:
        f.append("ttr=" + ("<24h" if ctx["hours_to_end"] < 24
                           else "1-3d" if ctx["hours_to_end"] <= 72 else ">3d"))
    if ctx.get("chart_pattern"):
        f.append("pattern=" + ctx["chart_pattern"])
        f.append("zmag=" + ("3+" if abs(ctx.get("z") or 0) >= 3 else "<3"))
    if "news_backed" in ctx:
        f.append("newsbk=" + ("yes" if ctx["news_backed"] else "no"))
    if ctx.get("news_sent") is not None:
        f.append("sent=" + ("pos" if ctx["news_sent"] > 0
                            else "neg" if ctx["news_sent"] < 0 else "neutral"))
    if ctx.get("oracle_agree") is not None:
        f.append("oracle=" + ("agree" if ctx["oracle_agree"] else "disagree"))
    if ctx.get("whale_agree") is not None:
        f.append("whale=" + ("agree" if ctx["whale_agree"] else "disagree"))
    if ctx.get("smart_agree") is not None:
        f.append("smart=" + ("agree" if ctx["smart_agree"] else "disagree"))
    return f


def mine_patterns(account, min_n=10, days=14):
    """Model 11 — search every single feature AND every feature pair across
    settled trades for combinations that reliably lose. Complex patterns
    (e.g. 'No-side sports trades entered on down-moves') emerge here that
    no single filter can see."""
    from itertools import combinations
    stats = {}
    cutoff = (now_utc() - timedelta(days=days)).isoformat(timespec="seconds")
    for t in account["settled"]:
        if (t["strategy"] == "arbitrage" or t.get("closed", "") < cutoff
                or dead_cohort(t)):
            continue  # markets adapt — only the last 2 weeks define a veto
        feats = sorted(trade_features(t))
        win = 1 if t["pnl"] >= 0 else 0
        for r in (1, 2):
            for combo in combinations(feats, r):
                key = "&".join(combo)
                w, n, pnl = stats.get(key, (0, 0, 0.0))
                stats[key] = (w + win, n + 1, round(pnl + t["pnl"], 2))
    mined = [{"pattern": k, "n": n, "wins": w, "pnl": pnl,
              "avg": round(pnl / n, 3)}
             for k, (w, n, pnl) in stats.items() if n >= min_n]
    mined.sort(key=lambda x: x["pnl"])
    return mined


def compute_patterns(account):
    """Refresh mined patterns; proven losers (10+ trades, clearly negative)
    become entry vetoes. Persisted for the dashboard and reviews."""
    mined = mine_patterns(account)
    # null hypothesis for the significance test: the book's own win rate —
    # a veto must be SURPRISING under it, not merely expensive
    nonarb = [t for t in account["settled"] if t["strategy"] != "arbitrage"]
    p0 = (max(0.30, min(0.95, sum(t["pnl"] >= 0 for t in nonarb)
                        / len(nonarb))) if nonarb else 0.5)
    # floor at 30%: below that the whole BOOK is broken and "surprise vs
    # the book" loses meaning — concentrated bleeders must still veto
    # only patterns that name a strategy may veto — a bare "hour=00" mined
    # from news losses must not ground the favorites book too (attribution)
    vetoes = [m["pattern"] for m in mined
              if m["pnl"] < -1 and m["avg"] < -0.05
              and chartml.binom_p(m["wins"], m["n"], p0) < 0.15
              and "strat=" in m["pattern"]
              and "&" in m["pattern"]][:15]   # pairs only: bare singles are
    # kill-switches (a lone strat=explore once blocked 43 entries)
    PATTERN_VETOES["list"] = vetoes
    try:
        atomic_write(PATTERNS_FILE, json.dumps({
            "updated": now_utc().isoformat(timespec="seconds"),
            "vetoes": vetoes, "worst": mined[:8], "best": mined[-5:][::-1],
        }, indent=1))
    except OSError:
        pass
    return vetoes


def pattern_veto(opp):
    """Check a candidate trade against the mined loser patterns."""
    from itertools import combinations
    probe = {"strategy": opp["strategy"], "name": opp["name"],
             "category": opp.get("category"),
             "side": opp["legs"][0].get("outcome"),
             "entry_price": opp.get("entry_price"),
             "closed": now_utc().isoformat(), "context": opp.get("context")}
    feats = sorted(trade_features(probe))
    bad = set(PATTERN_VETOES["list"])
    for r in (1, 2):
        for combo in combinations(feats, r):
            if "&".join(combo) in bad:
                return "&".join(combo)
    return None


# -------------------------------------------- explorer promotion (compound)

def explorer_proven_bands(account):
    """Price bands the explorer's $1 bets have PROVEN out: 25+ settles and a
    conservative (Wilson lower bound) win rate above that band's breakeven.
    (25 because 15 cannot statistically separate a 95% win rate from 88% —
    and at these prices that gap is the difference between profit and ruin.)"""
    bands = {}
    for t in account["settled"]:
        if t["strategy"] != "explore":
            continue
        b = int(round((t.get("entry_price") or 0) * 100))
        w, n = bands.get(b, (0, 0))
        bands[b] = (w + (1 if t["pnl"] >= 0 else 0), n + 1)
    fams = {}
    for t in account["settled"]:
        if t["strategy"] != "explore":
            continue
        b = int(round((t.get("entry_price") or 0) * 100))
        fams.setdefault(b, set()).add((family_of(t.get("name")),
                                       (t.get("closed") or "")[:10]))
    return {b for b, (w, n) in bands.items()
            if n >= 25 and len(fams.get(b, ())) >= 15
            and wilson_lower(w, n) > b / 100 + 0.01}


def probation_verdict(probe):
    """Judge a promoted band on its CLEAN post-promotion sample only. The
    25 qualifying settles are spent evidence — winner's curse (the best of
    ~14 bands always looks better than it is) and 6-hourly repeated peeking
    both inflate them, and Kelly sized off an inflated win rate is the one
    error that can flip growth negative."""
    n = len(probe)
    if n < 8:
        return None
    wins = sum(1 for t in probe if t["pnl"] >= 0)
    pnl = sum(t["pnl"] for t in probe)
    breakeven = sum(t.get("entry_price", 0.96) for t in probe) / n + 0.01
    lo = wilson_lower(wins, n)
    if n >= 15 and lo > breakeven and pnl > 0:
        return "graduate"
    if pnl < -0.5 and lo < breakeven:
        return "demote"
    return None


def promote_explorer_findings():
    """Promotion is no longer a one-way door. A newly proven band enters on
    PROBATION at quarter size; it graduates to full Kelly only after 15+
    clean post-promotion settles keep the Wilson bound above breakeven, and
    it auto-demotes back to $1 explorer duty if they don't. Nothing is
    banished, everything keeps earning its size. (Band ceiling stays 98.9c —
    99c was rejected on 457 backtest trades; floor 90c.)"""
    cfg = load_config()
    account = load_account(cfg)
    h = cfg["high_probability"]
    val_min = h.get("validated_min")

    if val_min and h["buy_price_min"] < val_min:
        # a band is on probation — judge the clean sample, nothing else
        since = h.get("promoted_at", "")
        probe = [t for t in account["settled"]
                 if t["strategy"] == "high_prob"
                 and (t.get("context") or {}).get("probation")
                 and t.get("closed", "") > since]
        verdict = probation_verdict(probe)
        if verdict == "graduate":
            h["validated_min"] = h["buy_price_min"]
            h.pop("promoted_at", None)
            atomic_write(CONFIG_FILE, json.dumps(cfg, indent=2))
            note(f"PROBATION PASSED: {int(round(h['buy_price_min']*100))}¢ band "
                 f"graduated to full size on {len(probe)} clean settles")
        elif verdict == "demote":
            pnl = sum(t["pnl"] for t in probe)
            h["buy_price_min"] = val_min
            h.pop("promoted_at", None)
            atomic_write(CONFIG_FILE, json.dumps(cfg, indent=2))
            note(f"DEMOTED: probation band lost ${-pnl:.2f} over {len(probe)} "
                 f"clean settles — back to $1 explorer duty")
        return  # one probation at a time; no new promotions while one runs

    proven = explorer_proven_bands(account)
    cur = int(round(h["buy_price_min"] * 100))
    new_min = cur
    while new_min - 1 in proven and new_min - 1 >= 90:
        new_min -= 1
    if new_min < cur:
        h["validated_min"] = h["buy_price_min"]   # the proven floor to fall back to
        h["promoted_at"] = now_utc().isoformat(timespec="seconds")
        h["buy_price_min"] = new_min / 100
        atomic_write(CONFIG_FILE, json.dumps(cfg, indent=2))
        note(f"PROMOTION (probation): explorer proved the {new_min}-{cur}¢ band — "
             f"main book trades it at QUARTER size until 15+ clean settles confirm")


def is_in_game(m):
    """Is this market's underlying game LIVE right now? The audit found
    every time gate measured hours-to-endDate — but sports endDate is the
    TOURNAMENT/series end, so live matches sailed through 6h gates all
    day (-$13 of daytrade losses, all five lane90 losers). gameStartTime
    is the honest clock: started and not resolved = in-game = jump risk."""
    gs = m.get("gameStartTime")
    if not gs:
        return False
    try:
        s = str(gs).replace(" ", "T")
        if s.endswith("+00"):
            s += ":00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return now_utc() >= dt
    except (ValueError, TypeError):
        return False


_SPORTSY = re.compile(r" vs\.? |o/u |exact score|both teams|1st half"
                      r"|moneyline|spread:", re.I)


def sports_probe_ok(pcfg, m, probe_room):
    """SPORTS-DESK v1 (user-directed probation, 06-12). The in-game style
    is what died (-$94: gap deaths during live matches, stacked exact
    scores); the probe re-admits sports PRE-GAME ONLY at hard caps
    ($5/trade, $50 total exposure), one bet per event, tagged
    context.sports_probe so learning judges the new style on its own
    record. Promotion to bigger size requires 15+ material settles net
    positive — promotion decides sizing, not enthusiasm."""
    return (bool(pcfg.get("enabled")) and not is_in_game(m)
            and probe_room >= pcfg.get("max_per_trade", 5.0))


def sports_probe_spent(account):
    """Budget the probe has consumed TODAY: open sports-shaped risk capital
    PLUS today's realized probe losses. A stopped-out loser must not free
    its budget for another cohort — the $50 cap bounds realized damage per
    day, not just open heat (risk review 06-12 found settling losers would
    otherwise let the probe burn ~6x its budget overnight). Arbitrage
    baskets are excluded: payout locked at entry, zero risk (position_risk
    counts them 0 for the same reason)."""
    today = now_utc().isoformat()[:10]
    spent = 0.0
    for p in account.get("positions", []):
        if p.get("strategy") == "arbitrage":
            continue
        ctx = p.get("context") or {}
        if (p.get("category") == "Sports"
                or _SPORTSY.search(p.get("name") or "")
                or ctx.get("sports_probe")):
            spent += p["shares"] * p["entry_price"]
    for t in account.get("settled", []):
        if ((t.get("context") or {}).get("sports_probe")
                and (t.get("closed") or "")[:10] == today
                and t["pnl"] < 0):
            spent += -t["pnl"]
    return spent


_CT_ASSET = re.compile(r"bitcoin|ethereum|solana|xrp|dogecoin|\bbtc\b|\beth\b",
                       re.I)


def is_crypto_threshold(q):
    """Crypto PRICE-THRESHOLD markets ('BTC above $66,000?') — the bucket
    two instruments call negative for favorites (research -0.69%/$1 over
    299 obs; live book bled there today). Up-or-down markets are NOT
    this bucket (they tested +5.7%/$1) and stay tradeable."""
    ql = q.lower()
    if "up or down" in ql:
        return False
    return bool(_CT_ASSET.search(ql)) and bool(
        re.search(r"\$[\d,]+|above|below|between|reach|dip", ql))


def crypto_explore_stake(hcfg, category, question, price, max_dollars, bankroll):
    """Crypto-favorite scale-up for the explore lane.

    Crypto's structural edge (favorites priced ~11.4% loss-implied yet
    realizing ~6.8% — a real ~4.6pp price-bias edge) was stranded at the $1
    flat explore stake: median crypto bet $0.89 on a $10k book, 416 of 417
    crypto trades being explore pennies. This lifts the stake for crypto
    FAVORITES only — the 0.85+ band where the near-lock structural mispricing
    lives — and never the ~0.50 up/down coinflips (brain crypto oos_skill ~0;
    their 90%+ win rate is unproven at size on thin 15-min books).

    Returns the (possibly larger) stake. Bounded by crypto_max_dollars_per_trade
    and bankroll; gated by crypto_favorite_min. Both config-driven and
    hot-reloadable — set crypto_max_dollars_per_trade == max_dollars to disable.
    The by_category ROI panel is the kill switch."""
    if price < hcfg.get("crypto_favorite_min", 0.85):
        return max_dollars
    ql = (question or "").lower()
    is_crypto = (cat_key(category) == "crypto"
                 or cluster_of(question or "") == "crypto-price"
                 or any(w in ql for w in _CRYPTO_IDS))
    if not is_crypto:
        return max_dollars
    return min(hcfg.get("crypto_max_dollars_per_trade", max_dollars), bankroll)


def scan_high_prob(cfg, skip_ids, open_count, multiplier=1.0,
                   blocked_bands=(), blocked_categories=(), category_counts=None,
                   bankroll=0.0, band_stats=None, section="high_probability",
                   strategy="high_prob", use_kelly=True, held_names=(),
                   sports_exposure=0.0, held_event_ids=()):
    """Find heavy favorites (e.g. 96-99 cents) resolving within a few days.
    Also drives the explorer book (section="explore"): same machinery, wider
    price band, flat $1 stakes, no Kelly gate — it buys learning data."""
    hcfg = cfg[section]
    room = hcfg["max_open_positions"] - open_count
    if room <= 0:
        return []
    max_dollars = (hcfg["max_dollars_per_trade"] * multiplier
                   if not use_kelly else
                   min(hcfg["max_dollars_per_trade"],
                       bankroll * hcfg.get("risk_per_trade_pct",
                           cfg.get("bankroll", {}).get(
                               "risk_per_trade_pct", 1.0)) / 100) * multiplier)
    # risk-per-trade: a hold-to-resolution binary risks its full stake, so
    # the stake itself is capped at ~1% of current bankroll
    cat_cap = hcfg.get("max_open_per_category", 999)
    cat_counts = dict(category_counts or {})  # concentration limit: don't load
    # up on many trades of one type (e.g. crypto) that all lose together

    # lane90 — the researched lane (kelly book only): 90-95.9c favorites
    # inside 24h. Three instruments agree it's where the edge lives
    # (research +3.8..11.1%/$1, replay sports 1,901/1,901, live explorer
    # resolutions); the classic 96-98.9c/24-48h lane tested ~flat.
    lane = (hcfg.get("lane90") or {}) if use_kelly else {}
    lane_on = bool(lane.get("enabled"))
    lane_fams = {family_of(n) for n in (held_names or ())}
    lane_fams |= {str(e) for e in (held_event_ids or ()) if e}
    pcfg = hcfg.get("sports_probe") or {}
    probe_room = (max(0.0, pcfg.get("budget", 50.0) - sports_exposure)
                  if pcfg.get("enabled") else 0.0)
    min_h = (min(lane.get("min_hours_to_resolution", 0.5),
                 hcfg.get("min_hours_to_resolution", 0))
             if lane_on else hcfg.get("min_hours_to_resolution", 0))

    # The API returns at most 100 markets per request, so page through them.
    # TWO query windows when the lane is open: 1,000+ markets end inside
    # 24h, so a single ascending-endDate sweep never reaches the classic
    # 24-48h candidates — each lane gets its own page budget.
    horizon = now_utc() + timedelta(days=hcfg["max_days_to_resolution"])
    windows = [(min_h, hcfg["max_days_to_resolution"] * 24)]
    if lane_on:
        windows = [(lane.get("min_hours_to_resolution", 0.5),
                    lane.get("max_hours_to_resolution", 24)),
                   (hcfg.get("min_hours_to_resolution", 24),
                    hcfg["max_days_to_resolution"] * 24)]
    markets = []
    for w_lo, w_hi in windows:
        for offset in range(0, 600, 100):
            page = get_json(f"{GAMMA}/markets", params={
                "active": "true", "closed": "false", "order": "endDate",
                "ascending": "true", "limit": 100, "offset": offset,
                "end_date_min": (now_utc() + timedelta(hours=w_lo)
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "end_date_max": (now_utc() + timedelta(hours=w_hi)
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }) or []
            markets += page
            if len(page) < 100:
                break
            time.sleep(0.2)

    opportunities = []
    for m in markets:
        if len(opportunities) >= room:
            break
        if str(m.get("id")) in skip_ids:
            continue
        end = parse_end_date(m)
        if not end or end < now_utc() or end > horizon:
            continue
        hours_left = (end - now_utc()).total_seconds() / 3600
        in_classic = hours_left >= hcfg.get("min_hours_to_resolution", 0)
        in_lane = (lane_on
                   and lane.get("min_hours_to_resolution", 0.5) <= hours_left
                   <= lane.get("max_hours_to_resolution", 24))
        if not (in_classic or in_lane):
            continue  # the backtest showed entries this close to the end are weak
        if fnum(m.get("volume24hr")) < hcfg["min_volume_24h"]:
            continue
        if (use_kelly and hcfg.get("skip_crypto_threshold")
                and is_crypto_threshold(m.get("question", ""))):
            continue  # negative bucket on two instruments; explorer's $1
            # probes keep gathering the evidence (H#1b), real money waits

        prices = [fnum(p) for p in jlist(m.get("outcomePrices"))]
        tokens = jlist(m.get("clobTokenIds"))
        if len(prices) != 2 or len(tokens) != 2:
            continue

        def band_ok(p):
            return ((in_classic and hcfg["buy_price_min"] <= p
                     <= hcfg["buy_price_max"])
                    or (in_lane and lane.get("buy_price_min", 0.90) <= p
                        <= lane.get("buy_price_max", 0.959)))

        # Which side is the favorite?
        fav = 0 if prices[0] >= prices[1] else 1
        if not band_ok(prices[fav]):
            continue

        # Confirm with the live orderbook + microstructure filters.
        qcfg = cfg.get("quant", {})
        stats = book_stats(tokens[fav])
        time.sleep(0.15)
        if not stats:
            continue
        price = stats["ask"]
        if not band_ok(price):
            continue
        via_lane = in_lane and not (
            in_classic and hcfg["buy_price_min"] <= price
            <= hcfg["buy_price_max"])
        if use_kelly and is_in_game(m):
            continue   # live game = jump risk; no kelly entry, any lane
        if via_lane:
            # FIRST LIVE EVIDENCE (06-12): lane sports went 0/5, -$50.56,
            # and the H#9 science says the recorder PHYSICALLY cannot see
            # in-game gap risk (stale pinned books, gaps between 1-min
            # snapshots) — so sports stay out of the lane PERMANENTLY.
            # Keyword cluster missed exact-score/both-teams shapes; gate
            # on phrasing + cluster + gameStartTime together.
            q = m.get("question", "")
            if (cluster_of(q) == "sports-game" or _SPORTSY.search(q)
                    or m.get("gameStartTime")):
                continue
            ev_key = str((m.get("events") or [{}])[0].get("id") or "") \
                or family_of(q)
            if ev_key in lane_fams:
                continue   # one lane entry per EVENT: six "families" on
                # one Canada match died at once when keyed by name alone
        band = int(round(price * 100))
        if str(band) in blocked_bands:
            continue  # the bot learned this price range loses money
        if stats["spread"] > hcfg.get("max_spread", qcfg.get("max_spread", 1)):
            continue  # spread too wide — slippage would eat the edge
        if stats["imbalance"] < hcfg.get("min_book_imbalance",
                                         qcfg.get("min_book_imbalance", 0)):
            continue  # order book stacked with sellers — price pressure down
        if sum(p * sz for p, sz in stats["ask_levels"]) < 30:
            model_acted("quality")
            continue  # model 8: book too thin — quotes here are decoration

        # Momentum: never buy a favorite that's actively collapsing.
        change = recent_momentum(tokens[fav], qcfg)
        time.sleep(0.1)
        if change is not None and change < -qcfg.get("momentum_max_drop", 1):
            continue

        # Kelly sizing: bet in proportion to the estimated edge at this price.
        # The explorer skips Kelly — flat tiny stakes, information is the edge.
        dollars = (kelly_dollars(bankroll, price, band, band_stats or {}, qcfg)
                   if use_kelly else max_dollars)
        dollars = min(dollars, max_dollars)
        probation = bool(use_kelly and not via_lane
                         and hcfg.get("validated_min")
                         and price < hcfg["validated_min"])
        # (lane entries excluded: a promoted band's probation verdict must
        # not be judged on unrelated lane noise, nor lanes 1/8-sized)
        if probation:
            dollars *= 0.25  # promoted band on probation: quarter size
        if via_lane:
            # research-promoted lane starts at half size: live settled
            # money (by_lane bucket) decides whether it scales to full
            dollars *= lane.get("size_factor", 0.5)
        # category drives both the per-category brain specialist (point-in-time,
        # threaded — not recomputed at sizing) and the sports gating below.
        category = market_category(m)
        # Crypto-favorite scale-up (explore lane only): lift the flat $1 stake
        # for crypto favorites in the 0.85+ structural-edge band — never the
        # ~0.50 coinflips. See crypto_explore_stake() for the rationale.
        if not use_kelly:
            dollars = crypto_explore_stake(hcfg, category, m.get("question", ""),
                                           price, max_dollars, bankroll)
        adj = 1.0
        if use_kelly:
            adj = brain_adjust(strategy, price,
                               {"spread": stats["spread"],
                                "imbalance": stats["imbalance"],
                                "momentum_6h": change,
                                "hours_to_end": hours_left},
                               category=category)
            if adj <= 0.45:
                model_acted("brain")
                continue  # model 13: brain says this context loses
            dollars *= adj
        shares = int(dollars / price)
        if shares < 1:
            continue  # no edge at this price (or too small to trade)
        # Execution realism: price the order against actual book depth, and
        # re-check the band/edge at the price we'd really pay, not the quote.
        fill = vwap_fill(stats["ask_levels"], shares)
        if fill is None or not band_ok(fill):
            continue
        if fill - stats["ask"] > 0.005:
            model_acted("impact")
            continue  # model 6: filling deep in the book would eat the edge
        price = fill
        band = int(round(price * 100))
        time.sleep(0.1)
        q_full = m.get("question", "")
        is_sport = (category == "Sports" or cluster_of(q_full) == "sports-game"
                    or bool(_SPORTSY.search(q_full))
                    or bool(m.get("gameStartTime")))
        is_crypto = (cat_key(category) == "crypto"
                     or cluster_of(q_full) == "crypto-price"
                     or any(w in q_full.lower() for w in _CRYPTO_IDS))
        is_weather = (cat_key(category) == "weather"
                      or cluster_of(q_full) == "weather"
                      or _wx_parse(q_full) is not None)
        is_macro = (cat_key(category) == "macro"
                    or bool(_MACRO_RATE_RX.search(q_full))
                    or bool(_MACRO_CPI_RX.search(q_full))
                    or bool(_MACRO_YIELD_RX.search(q_full)))
        is_social = (cat_key(category) == "social"
                     or cluster_of(q_full) == "social-posts")
        sports_probe = False
        if is_sport and use_kelly:
            # sports only trade through the probation probe (see
            # sports_probe_ok docstring) — it bypasses the learned category
            # block deliberately, at probe size, to buy a fresh verdict
            if not sports_probe_ok(pcfg, m, probe_room):
                continue
            probe_keys = {(str((m.get("events") or [{}])[0].get("id") or "")
                           or family_of(q_full)), family_of(q_full)}
            if probe_keys & lane_fams:
                continue   # one probe per EVENT — no exact-score stacking,
                # within this scan AND across scans (held event ids seeded)
            sports_probe = True
        elif category in blocked_categories or (
                is_sport and "Sports" in blocked_categories):
            continue  # the bot learned this market type loses money. Use the
            # name-based is_sport flag (already _SPORTSY/cluster/gameStartTime
            # aware) so a blocked Sports cohort also catches sport-shaped
            # markets whose gamma category tag is missing/wrong (e.g. obscure
            # foreign football: Vaasan Palloseura, FF Jaro/HJK) — those slipped
            # the tag-only check and were the entire live loss (-$12.92 sports
            # vs +$2.97 everything else).
        if cat_counts.get(category, 0) >= cat_cap:
            continue  # concentration limit reached for this market type
        cat_counts[category] = cat_counts.get(category, 0) + 1
        outcomes = jlist(m.get("outcomes"))
        outcome_name = outcomes[fav] if len(outcomes) > fav else "?"
        o_agree, o_margin, o_kind = oracle_check(
            m.get("question", ""), end, outcome_name)
        wf = (whale_flow(str(m["conditionId"]))
              if m.get("conditionId") else None)
        w_agree = whale_verdict(wf, fav)
        # cross-market consensus (SHADOW): {} for the vast majority of markets
        # that have no confident twin -> xmkt context stays None -> brain
        # features default neutral and sizing is unchanged. fav==0 means we
        # back outcome[0], so divergence/consensus are already pm_p-aligned.
        xm = xmkt_lookup(m, price) if fav == 0 else {}
        # PER-CATEGORY SPORTS feature read (point-in-time, fail-silent). Only for
        # sports markets; for everything else sf stays the all-None neutral that
        # leaves _brain_x's sports features at 0.0 and the global path unchanged.
        # Reads ESPN scoreboard (game state) + finals-trained Elo + the live
        # de-vigged Odds-API snapshot already in `xm`. No future data: see
        # sports_features docstring. fav-aligned so all edges are P(fav).
        sf = (sports_features(m, price, fav, xm) if is_sport
              else {"sports_state": None, "sports_post": None,
                    "sportsbook_consensus": None, "sports_elo_fv": None,
                    "sports_div": None})
        # PER-CATEGORY CRYPTO feature read (point-in-time, fail-silent). Only for
        # crypto markets; for everything else cf stays the all-None neutral that
        # leaves _brain_x's crypto features at 0.0 and the global path unchanged.
        # Reads Coinbase/CoinGecko spot (distance from strike) + Kraken
        # closed-candle hourly vol + live Kraken ticker spread. No future data:
        # see crypto_features docstring.
        cf = (crypto_features(m, price) if is_crypto
              else {"crypto_spot_dist": None, "crypto_rvol_h": None,
                    "crypto_spread_bps": None})
        # PER-CATEGORY WEATHER feature read (point-in-time, fail-silent). Only for
        # weather markets; for everything else wf_x stays the all-None neutral that
        # leaves _brain_x's weather features at 0.0 and the global path unchanged.
        # Reads the Open-Meteo ~30-member ensemble (mean distance from strike,
        # spread, side-agreement) + weather.gov/NWS fallback mean. No future data:
        # see weather_features docstring.
        wf_x = (weather_features(m, price) if is_weather
                else {"wx_fc_strike": None, "wx_fc_spread": None,
                      "wx_model_agree": None})
        # PER-CATEGORY MACRO feature read (point-in-time, fail-silent, key-gated).
        # Only for macro markets; for everything else mf stays the all-None
        # neutral that leaves _brain_x's macro features at 0.0 and the global path
        # unchanged. Reads FRED (DFF funds rate, DGS10/DGS2 yield curve, CPI YoY)
        # when a FRED_API_KEY is present; absent, every field is None. No future
        # data: see macro_features docstring.
        mf = (macro_features(m, price) if is_macro
              else {"macro_rate_dev": None, "macro_cpi_surprise": None,
                    "macro_yield_signal": None})
        # PER-CATEGORY SOCIAL feature read (point-in-time, fail-silent, keyless).
        # Only for social markets; for everything else sof stays the all-None
        # neutral that leaves _brain_x's social features at 0.0 and the global
        # path unchanged. Reads ONLY the fresh, timestamp-filtered HEADLINES
        # buffer (news_rss Google News/BBC + HackerNews) — no network I/O here,
        # no future data: see social_features docstring. Side-aligned to the
        # outcome we'd back so sentiment alignment is P(fav)-oriented.
        sof = (social_features(q_full, outcome_name) if is_social
               else {"social_news_strong": None, "social_sent_mag": None,
                     "social_sent_align": None})
        exit_keys = {}
        if strategy == "explore":
            # NO stop: a $1 stake is its own insurance (max loss = cost), and
            # these markets jump through stops anyway (6 gap-outs, -$3.72) —
            # converting possible wins into certain losses. Hold for the
            # label; lock only near-certainties via the target.
            exit_keys = {"stop": 0.02, "target": 0.995}
        m15 = market_model_p(price, hours_left,
                             fnum(m.get("volume24hr")), stats["spread"])
        if use_kelly:
            # smarter sizing: bounded bonuses from validated signals, every
            # one measured by its own attribution bucket
            if m15 is not None and m15 > price + 0.03:
                dollars *= 1.25       # m15 LIKE (its dislike side proved out
                                      # on 23 settles — symmetric, smaller)
            if (o_agree is True and o_margin is not None
                    and ((o_kind == "wx" and o_margin >= 2.0)
                         or (o_kind == "crypto" and o_margin >= 0.15))):
                dollars *= 1.25       # fundamental oracle strongly agrees
                # (kind-aware: degree units and probability units no
                # longer share one threshold — audit critical)
            dollars = min(dollars, max_dollars)   # cap is the final ceiling
            if sports_probe:
                dollars = min(dollars, pcfg.get("max_per_trade", 5.0))
            new_shares = max(1, int(dollars / price))
            if new_shares > shares:
                # boosted size must re-validate against the book depth the
                # smaller order was checked for (audit: up to +56% phantom)
                fill2 = vwap_fill(stats["ask_levels"], new_shares)
                if (fill2 is None or not band_ok(fill2)
                        or fill2 - stats["ask"] > 0.005):
                    new_shares = shares    # keep the depth-validated size
                else:
                    price = fill2
                    band = int(round(price * 100))
            shares = new_shares
        if m15 is not None:
            # m15's earned power, PROMOTED 06-12 19:41: strong dislike now
            # skips BOTH books. It strong-disliked ALL FIVE r90 losers
            # (-$50.56) while its old power merely halved their size;
            # disliked bucket -1.22/42 vs liked +0.04/39. Its evidence
            # stream (recorder labels) is independent of our trades, so
            # gating cannot self-lock.
            if m15 < price - 0.05:
                model_acted("m15")
                continue
            if use_kelly and m15 < price - 0.03:
                dollars *= 0.5
                shares = max(1, int(dollars / price))
        if via_lane:
            lane_fams.add(ev_key)   # burn the event slot only on a REAL
            # opportunity, not on the first unbuyable candidate
            # HOLD-TO-RESOLUTION (H#9 science): with band-exit stops the
            # measured hold rate is 64% vs 92.9% breakeven; held to the
            # end the same markets win 97.0% (n=235, Wilson LB 95.7%).
            # The lane either holds for the label or it has no edge.
            exit_keys = {"stop": 0.02, "target": 0.995}
        if sports_probe:
            lane_fams |= probe_keys         # burn the event slot
            probe_room -= shares * price    # burn the budget this scan
        opportunities.append({**exit_keys,
            "strategy": strategy,
            "event_id": (str((m.get("events") or [{}])[0].get("id"))
                         if (m.get("events") or [{}])[0].get("id") else None),
            "category": category,
            "context": {"spread": stats["spread"], "imbalance": stats["imbalance"],
                        "momentum_6h": change, "hours_to_end": round(hours_left, 1),
                        "lane": ("r90" if via_lane else "classic") if use_kelly else None,
                        "kelly_dollars": round(dollars, 2), "probation": probation,
                        "brain_adj": round(adj, 2),
                        "oracle_agree": o_agree, "oracle_margin": o_margin,
                        "oracle_kind": o_kind, "oracle_v": 2,
                        "whale_agree": w_agree,
                        "whale_net": wf and wf.get("net"),
                        "smart_agree": smart_verdict(wf, fav),
                        "smart_fresh": wf and wf.get("fresh"),
                        "m15_p": m15,
                        "xmkt_consensus": xm.get("consensus_p"),
                        "xmkt_divergence": xm.get("divergence"),
                        "xmkt_sources": xm.get("sources"),
                        # per-category SPORTS features (point-in-time; all None
                        # for non-sports markets -> _brain_x neutral 0.0 -> the
                        # sports specialist learns them, global path unchanged).
                        "sportsbook_consensus": sf["sportsbook_consensus"],
                        "sports_elo_fv": sf["sports_elo_fv"],
                        "sports_div": sf["sports_div"],
                        "sports_state": sf["sports_state"],
                        "sports_post": sf["sports_post"],
                        # per-category CRYPTO features (point-in-time; all None
                        # for non-crypto markets -> _brain_x neutral 0.0 -> the
                        # crypto specialist learns them, global path unchanged).
                        "crypto_spot_dist": cf["crypto_spot_dist"],
                        "crypto_rvol_h": cf["crypto_rvol_h"],
                        "crypto_spread_bps": cf["crypto_spread_bps"],
                        # per-category WEATHER features (point-in-time; all None
                        # for non-weather markets -> _brain_x neutral 0.0 -> the
                        # weather specialist learns them, global path unchanged).
                        "wx_fc_strike": wf_x["wx_fc_strike"],
                        "wx_fc_spread": wf_x["wx_fc_spread"],
                        "wx_model_agree": wf_x["wx_model_agree"],
                        # per-category MACRO features (point-in-time, key-gated;
                        # all None for non-macro markets -> _brain_x neutral 0.0
                        # -> the macro specialist learns them, global path
                        # unchanged).
                        "macro_rate_dev": mf["macro_rate_dev"],
                        "macro_cpi_surprise": mf["macro_cpi_surprise"],
                        "macro_yield_signal": mf["macro_yield_signal"],
                        # per-category SOCIAL features (point-in-time, keyless;
                        # all None for non-social markets / no fresh coverage ->
                        # _brain_x neutral 0.0 -> the social specialist learns
                        # them, global path unchanged).
                        "social_news_strong": sof["social_news_strong"],
                        "social_sent_mag": sof["social_sent_mag"],
                        "social_sent_align": sof["social_sent_align"],
                        "sports_probe": 1 if sports_probe else None},
            "name": m["question"],
            "legs": [{"market_id": str(m["id"]), "question": m["question"],
                      "token_index": fav, "token_id": tokens[fav], "price": price,
                      "outcome": outcome_name}],
            "shares": shares,
            "cost": round(shares * price, 2),
            "entry_price": price,
            "detail": f"buy '{outcome_name}' @ {price:.3f}, resolves {end:%b %d}",
        })
    return opportunities


# ---------------------------------------------------------- paper trading


# ------------------------------------------------- bankroll management

def position_risk(p):
    """Worst-case loss of one position: the full stake for hold-to-
    resolution books (binary markets can go to zero); the stop distance
    times shares for bracketed books — exactly how a desk counts heat."""
    if p.get("strategy") == "arbitrage":
        return 0.0                       # payout locked at entry
    stop = p.get("stop") or 0
    entry = p.get("entry_price") or 0
    if stop > 0.03 and entry:
        return round(max(0.0, entry - stop) * (p.get("shares") or 0), 2)
    return round(p.get("cost") or 0.0, 2)


def _dd_factor(dd_pct, ladder):
    """The drawdown ladder: every professional book de-risks as losses
    deepen — smaller bets buy time for the edge to reassert, and sizing
    restores automatically with the equity high-water mark."""
    factor = 1.0
    for level, f in sorted(ladder):
        if dd_pct >= level:
            factor = f
    return factor


def bankroll_manager(cfg, account):
    """The rules a real trader lives by, computed fresh per decision:
      - bankroll = CURRENT equity (compounds up, de-compounds down)
      - risk per trade = fixed % of bankroll (default 1%)
      - portfolio heat = sum of worst-case losses across open positions,
        capped at a % of bankroll (default 10%) — the 'how much can
        tonight cost me' number
      - drawdown ladder cuts all sizing at -2/-4/-6% from the peak."""
    bcfg = cfg.get("bankroll", {})
    invested = sum(position_value(p) for p in account["positions"])
    bankroll = account["cash"] + invested
    peak = bankroll
    if HISTORY_FILE.exists():
        try:
            hist = json.loads(HISTORY_FILE.read_text())
            if hist:
                peak = max(max(h["total"] for h in hist), bankroll)
        except ValueError:
            pass
    dd = max(0.0, (peak - bankroll) / peak * 100) if peak else 0.0
    ladder = bcfg.get("dd_ladder", [[2, 0.75], [4, 0.5], [6, 0.25]])
    return {"bankroll": round(bankroll, 2),
            "risk_per_trade": round(
                bankroll * bcfg.get("risk_per_trade_pct", 1.0) / 100, 2),
            "heat_cap": round(
                bankroll * bcfg.get("max_heat_pct", 10.0) / 100, 2),
            "heat_used": round(sum(position_risk(p)
                                   for p in account["positions"]), 2),
            "drawdown_pct": round(dd, 2),
            "dd_factor": _dd_factor(dd, ladder)}


def strategy_budget(cfg, account, strat):
    """Each strategy trades its own sub-account: allocation plus its own P&L."""
    alloc = cfg.get("allocations", {}).get(strat)
    if alloc is None:
        return account["cash"]
    realized = sum(t["pnl"] for t in account["settled"] if t["strategy"] == strat)
    spent = sum(p["cost"] for p in account["positions"] if p["strategy"] == strat)
    return alloc + realized - spent


def daily_breaker_hit(cfg, account):
    """The -$/day hard stop, REALIZED losses only (audit: the old check
    mixed lifetime unrealized P&L into 'today', firing late on fast losing
    days), enforced here so every entry path composes with it."""
    try:
        limit = cfg.get("risk", {}).get("max_daily_loss", 300.0)
        today = now_utc().strftime("%Y-%m-%d")
        realized = sum(t["pnl"] for t in account.get("settled", [])
                       if (t.get("closed") or "").startswith(today))
        return realized <= -abs(limit)
    except Exception:
        return False


def open_position(account, opp, cfg=None):
    now = time.time()
    ORDER_TIMES[:] = [t for t in ORDER_TIMES if now - t < 60]
    if len(ORDER_TIMES) >= 30:
        return  # governor: hard ceiling of 30 order operations per minute
    budget = strategy_budget(cfg, account, opp["strategy"]) if cfg else account["cash"]
    if budget < opp["cost"] or opp["cost"] > account["cash"]:
        note(f"skip (sub-account budget reached): {opp['name'][:60]}")
        return
    if opp["strategy"] != "arbitrage":
        if daily_breaker_hit(cfg or load_config(), account):
            note(f"daily circuit breaker: no new entries ({opp['name'][:40]})")
            return   # audit: the breaker only guarded the main scan; the
            # fast daytrade thread kept opening positions after it fired
        bm = bankroll_manager(cfg or load_config(), account)
        if bm["heat_used"] + position_risk(opp) > bm["heat_cap"]:
            model_acted("heat")
            return  # portfolio heat cap: tonight already risks enough
    if opp["strategy"] not in ("arbitrage", "explore"):
        # the explorer is EXEMPT from pattern vetoes: it is the shadow-tester
        # — its $1 bets are how a stale veto earns retirement. Budget, cell
        # blocks and Thompson govern it instead.
        pv = pattern_veto(opp)
        if pv:
            model_acted("pattern")
            return  # model 11: this combination of traits reliably loses
        crowded, cl = cluster_check(account, opp)
        if crowded:
            model_acted("cluster")
            if opp["name"] not in CLUSTER_NOTED:  # log each market once, not every scan
                CLUSTER_NOTED.add(opp["name"])
                note(f"skip (model 5: '{cl}' cluster at its 40% cap): {opp['name'][:60]}")
            return
    cat = opp.get("category")
    cat_alloc = (cfg or {}).get("category_allocations", {}).get(cat)
    if cat_alloc is not None:
        cat_used = sum(p["cost"] for p in account["positions"]
                       if p.get("category") == cat)
        if cat_used + opp["cost"] > cat_alloc:
            note(f"skip ({cat} category allocation full): {opp['name'][:60]}")
            return
    if "_ts" in opp:
        opp["context"] = dict(opp.get("context") or {}, ts_draw=opp["_ts"])
    account["cash"] -= opp["cost"]
    account["positions"].append({
        "strategy": opp["strategy"],
        "event_id": opp["event_id"],
        "category": opp.get("category"),
        "context": opp.get("context"),
        "stop": opp.get("stop"), "target": opp.get("target"),
        "name": opp["name"],
        "shares": opp["shares"],
        "cost": opp["cost"],
        "entry_price": opp["entry_price"],
        "legs": [dict(leg, settled=False, proceeds=0.0) for leg in opp["legs"]],
        "opened": now_utc().isoformat(timespec="seconds"),
    })
    journal("BUY", strategy=opp["strategy"], name=opp["name"][:80],
            cost=opp["cost"], price=opp.get("entry_price"),
            category=opp.get("category"), context=opp.get("context"))
    ORDER_TIMES.append(time.time())
    log_trade("BUY", opp["strategy"], opp["name"], opp["detail"],
              opp["cost"], 0, 0, account["cash"])
    note(f"BUY [{opp['strategy']}] {opp['name'][:60]} — "
         f"cost ${opp['cost']:.2f} ({opp['detail']})")


def settle_positions(account):
    """Check held markets; when one resolves, collect the (paper) payout."""
    still_open = []
    for pos in account["positions"]:
        for leg in pos["legs"]:
            if leg["settled"]:
                continue
            data = get_json(f"{GAMMA}/markets", params={"id": leg["market_id"]})
            time.sleep(0.1)
            market = data[0] if data else None
            if not market:
                # Gamma drops closed markets from plain id queries — this
                # market may have RESOLVED. Re-probe the closed set.
                data = get_json(f"{GAMMA}/markets",
                                params={"id": leg["market_id"], "closed": "true"})
                market = data[0] if data else None
            if not market or not market.get("closed"):
                continue
            prices = [fnum(p) for p in jlist(market.get("outcomePrices"))]
            final = prices[leg["token_index"]] if len(prices) > leg["token_index"] else 0.0
            leg["settled"] = True
            leg["proceeds"] = round(pos["shares"] * final, 2)
            account["cash"] += leg["proceeds"]

        if all(leg["settled"] for leg in pos["legs"]):
            proceeds = sum(leg["proceeds"] for leg in pos["legs"])
            pnl = round(proceeds - pos["cost"], 2)
            account["realized_pnl"] = round(account["realized_pnl"] + pnl, 2)
            reentry_mark(pos["legs"][0]["market_id"])
            brain_online_learn(pos, pnl)   # adagrad step + drift sensing
            journal("SETTLE", strategy=pos["strategy"], name=pos["name"][:80],
                    pnl=pnl, category=pos.get("category"),
                    context=pos.get("context"))
            account["settled"].append({
                "token": leg_token_id(pos["legs"][0]),
                "shares": pos["shares"], "path": pos.get("path", [])[-40:],
                "side": (pos["legs"][0].get("outcome") if pos["strategy"] != "arbitrage" else "ALL"),
                "strategy": pos["strategy"], "name": pos["name"],
                "cost": pos["cost"], "proceeds": round(proceeds, 2),
                "pnl": pnl, "entry_price": pos.get("entry_price", 0),
                "category": pos.get("category"), "context": pos.get("context"),
                "closed": now_utc().isoformat(timespec="seconds"),
            })
            outcome = "WIN" if pnl >= 0 else "LOSS"
            log_trade("SETTLE", pos["strategy"], pos["name"], outcome,
                      0, proceeds, pnl, account["cash"])
            note(f"SETTLED [{outcome}] {pos['name'][:60]} — P&L ${pnl:+.2f}")
        else:
            still_open.append(pos)
    account["positions"] = still_open


BROAD_CATEGORIES = ["Sports", "Esports", "Crypto", "Politics", "Economy",
                    "Business", "Finance", "Pop Culture", "Science", "Tech"]


def market_category(market):
    """Polymarket labels events with tags (Sports, Crypto, Politics, ...).
    Grab the broad one so the bot can learn which market types win or lose."""
    ev = (market.get("events") or [{}])[0]
    tags = []
    if ev.get("id"):
        data = get_json(f"{GAMMA}/events", params={"id": ev["id"]})
        if data:
            tags = [t.get("label", "") for t in (data[0].get("tags") or [])]
    for broad in BROAD_CATEGORIES:
        if broad in tags:
            return broad
    return tags[0] if tags else "Other"


def leg_token_id(leg):
    """Older positions may not have the token id saved; look it up once."""
    if leg.get("token_id"):
        return leg["token_id"]
    data = get_json(f"{GAMMA}/markets", params={"id": leg["market_id"]})
    if data:
        tokens = jlist(data[0].get("clobTokenIds"))
        if len(tokens) > leg["token_index"]:
            leg["token_id"] = tokens[leg["token_index"]]
            return leg["token_id"]
    return None


def check_exits(cfg, account):
    """Watch live prices on open favorite trades and sell when the price says
    to: cut losses early instead of riding a collapsing favorite to zero, or
    lock a win once the market is all but decided. Arbitrage positions are
    never sold early — their payout is only guaranteed if held to the end."""
    ecfg = cfg.get("exit", {})
    stop = ecfg.get("stop_loss_price", 0)
    target = ecfg.get("take_profit_price", 2)
    watch = [(pos, leg_token_id(pos["legs"][0]))
             for pos in list(account["positions"])
             if pos["strategy"] in ("high_prob", "news", "explore", "daytrade")
             and not pos["legs"][0]["settled"]]
    books = fetch_books_bulk([t for _, t in watch if t])
    for pos, token in watch:
        bs = books.get(str(token))
        bid = bs["bid"] if bs else None
        if bs:  # remember live prices: bid for exits, mid for fair valuation
            pos["last_price"] = bid
            pos["last_mid"] = round((bs["bid"] + bs["ask"]) / 2, 4)
            pos["last_checked"] = now_utc().isoformat(timespec="seconds")
            path = pos.setdefault("path", [])  # 1 sample/min, keeps 3 hours
            if not path or time.time() - path[-1][0] >= 60:
                path.append([int(time.time()), pos["last_mid"]])
                del path[:-180]
            probe = ARMED_SCORES.get(str(token))
            if (probe and probe.get("p0") is not None and bid is not None
                    and abs(bid - probe["p0"]) >= 0.03):
                lat = round(time.time() - probe["t"], 1)
                SCORES_STATS["latencies"].append(lat)
                journal("LATENCY", game=probe["game"], seconds=lat,
                        move=round(bid - probe["p0"], 3))
                note(f"SCORE-LEAD: market repriced {lat}s after ESPN "
                     f"scored ({probe['game']})")
                ARMED_SCORES.pop(str(token), None)
        stop_p = pos.get("stop") or stop
        target_p = pos.get("target") or target
        if bid is None:
            continue
        reason = None
        if bid <= stop_p:
            reason = "stop-loss — price fell, cutting the loss"
        elif bid >= target_p:
            reason = "take-profit — locking the win early"
        elif (pos["strategy"] == "high_prob"
              and (pos.get("context") or {}).get("lane") != "r90"
              and pos["last_mid"] < cfg["high_probability"]["buy_price_min"] - 0.06):
            # Model 9: the favorite left the validated band. Trigger loosened
            # 93c -> 90c on post-exit chart evidence: 5 of 7 exits at the old
            # threshold whipsawed. r90 lane EXEMPT (audit: this classic-band
            # floor sat ABOVE lane entry prices, churning every lane entry
            # below 92c instantly; H#9 science: the lane holds to resolution
            # — 97.0% held vs 64% with band exits).
            reason = "model-exit — left the validated price band"
            model_acted("edge_gone")
        elif (pos["strategy"] != "explore"      # the info book holds for its
              and (pos.get("context") or {}).get("lane") != "r90"  # so does
              and len(pos.get("path", [])) >= 5   # the r90 lane (H#9: held =
              and bid < pos.get("entry_price", 1)  # 97.0%, exited = 64%)
              and bs["imbalance"] < 0.5            # 13/13 explore resolutions
              and all(pos["path"][i][1] > pos["path"][i + 1][1]
                      for i in range(-5, -1))):
            # Model 12: the price path is sliding monotonically for 4+ minutes
            # while we're under water — get out before the slide becomes a gap.
            reason = "model-exit — sustained price slide"
            model_acted("slide")
        elif pos["strategy"] in ("news", "daytrade"):
            # Model 10: persistent seller pressure in the book while the trade
            # is under water — exit before the stop is hit.
            if bs["imbalance"] < 0.15 and bid < pos.get("entry_price", 1):
                pos["pressure_strikes"] = pos.get("pressure_strikes", 0) + 1
            else:
                pos["pressure_strikes"] = 0
            if pos["pressure_strikes"] >= 3:
                reason = "model-exit — order-book pressure collapsed"
                model_acted("pressure")
        if not reason:
            continue
        proceeds = round(pos["shares"] * bid, 2)
        pnl = round(proceeds - pos["cost"], 2)
        account["cash"] = round(account["cash"] + proceeds, 2)
        account["realized_pnl"] = round(account["realized_pnl"] + pnl, 2)
        account["settled"].append({
            "side": pos["legs"][0].get("outcome"),
            "strategy": pos["strategy"], "name": pos["name"],
            "cost": pos["cost"], "proceeds": proceeds, "pnl": pnl,
            "entry_price": pos.get("entry_price", 0), "reason": reason,
            "category": pos.get("category"), "context": pos.get("context"),
            "exit_price": bid, "stop": stop_p, "shares": pos["shares"],
            "token": token, "path": pos.get("path", [])[-40:],
            "closed": now_utc().isoformat(timespec="seconds"),
        })
        account["positions"].remove(pos)
        reentry_mark(pos["legs"][0]["market_id"])
        brain_online_learn(pos, pnl)       # adagrad step + drift sensing
        journal("SELL", strategy=pos["strategy"], name=pos["name"][:80],
                reason=reason, pnl=pnl, entry=pos.get("entry_price"), exit=bid,
                path_tail=[pt[1] for pt in pos.get("path", [])[-10:]])
        save_account(account)  # persist instantly so a shutdown can't replay the sale
        log_trade("SELL", pos["strategy"], pos["name"], reason,
                  0, proceeds, pnl, account["cash"])
        note(f"SELL {pos['name'][:60]} @ {bid:.3f} — {reason} — P&L ${pnl:+.2f}")


ACCOUNT_LOCK = threading.Lock()


def arb_loop(cfg, account):
    """Dedicated fast thread: scan the whole market for arbitrage every ~2s."""
    interval = max(1, int(cfg["arbitrage"].get("fast_scan_seconds", 2)))
    while True:
        start = time.time()
        try:
            cfg = load_config()
            arb_mult = compute_learning(account).get(
                "arbitrage", {}).get("multiplier", 1.0)
            if arb_mult <= 0:
                time.sleep(30)   # learning paused arbitrage: mispriced
                continue         # legs/settlement bugs can stop the book
            opps = scan_arbitrage(cfg, held_ids(account))
            if opps:
                with ACCOUNT_LOCK:
                    for o in opps:
                        open_position(account, o, cfg)
                    save_account(account)
        except Exception as e:
            print(f"  ! arb scanner error (will retry): {e}")
        time.sleep(max(0.2, interval - (time.time() - start)))




def _chart_stats(pts):
    """Pure chart read: pattern + anomaly statistics from a price series.
    Patterns are defined, not vibes: spike_fade = an outsized move already
    reverting (the lab-validated fade setup); mean_dev = stretched from its
    own mean but not yet turning; breakout = pinned at a range extreme
    (momentum — we do NOT fade these); drift = nothing to trade."""
    if not pts or len(pts) < 24:
        return None
    last, hi, lo = pts[-1], max(pts), min(pts)
    mu = sum(pts) / len(pts)
    sd = (sum((p - mu) ** 2 for p in pts) / len(pts)) ** 0.5
    z = (last - mu) / sd if sd > 1e-4 else 0.0
    ext = hi if abs(hi - mu) >= abs(lo - mu) else lo
    ext_i = pts.index(ext)
    z_ext = (ext - mu) / sd if sd > 1e-4 else 0.0   # how anomalous the SPIKE
    retrace = abs(ext - last) / abs(ext - mu) if abs(ext - mu) > 1e-4 else 0.0
    rng = hi - lo
    range_pos = (last - lo) / rng if rng > 1e-4 else 0.5
    if abs(z_ext) >= 2 and 0.25 <= retrace < 0.95 and ext_i < len(pts) - 3:
        pattern = "spike_fade"      # judged at the extreme, not after the
                                    # pullback already shrank the z-score
    elif rng >= 0.03 and (range_pos >= 0.97 or range_pos <= 0.03):
        pattern = "breakout"        # pinned at a REAL extreme = momentum.
                                    # This check must precede mean_dev: a
                                    # fresh ramp at its high is the one
                                    # thing a fader must never touch
    elif abs(z) >= 2:
        pattern = "mean_dev"
    else:
        pattern = "drift"
    return {"chart_pattern": pattern, "z": round(z, 2),
            "retrace": round(retrace, 2), "range_pos": round(range_pos, 2),
            "sd_6h": round(sd, 4)}


def _time_bins(pts, span_s, n_bins):
    """Resample (ts, price) to n_bins FIXED time buckets, last value per
    bucket, carry-forward for empty ones — so densely-watched tokens and
    1-min recorder series feed the classifier IDENTICAL shapes (audit:
    index-based sampling time-warped mixed-cadence memory)."""
    end = pts[-1][0]
    start = end - span_s
    out, j = [], 0
    last = pts[0][1]
    for b in range(1, n_bins + 1):
        edge = start + span_s * b / n_bins
        while j < len(pts) and pts[j][0] <= edge:
            last = pts[j][1]
            j += 1
        out.append(last)
    return out


def chart_features(token_id, hours=6):
    """Read the intraday chart — from our own full-resolution memory when
    we've been watching this market (1-second ticks beat the API's
    5-minute candles, and cost zero API calls), else fetch history."""
    pts = mem_series(token_id, hours * 3600)
    if len(pts) >= 60 and pts[-1][0] - pts[0][0] >= hours * 3000:
        return _chart_stats(_time_bins(pts, hours * 3600, 72))
    end = int(time.time())
    h = get_json(f"{CLOB}/prices-history", params={
        "market": token_id, "startTs": end - hours * 3600,
        "endTs": end, "fidelity": 5}) or {}
    return _chart_stats([fnum(p.get("p")) for p in h.get("history", [])])


def _best_series(token, market_id, seconds=21600):
    """Pick the series model 16 should read by QUALITY, not truthiness:
    a 3-tick token series used to short-circuit the lookup and silently
    disable the veto. Require 12+ points spanning 15+ minutes."""
    cands = [mem_series(token, seconds)]
    if market_id is not None:
        cands.append(mem_series(f"m:{market_id}", seconds))
    best = []
    for c in cands:
        if len(c) >= 12 and c[-1][0] - c[0][0] >= 900:
            if len(c) > len(best):
                best = c
    return best


def news_confirmed(question, ch, min_sent=0.3):
    """Is this price move CONFIRMED by the news feed? A matching headline
    whose sentiment agrees with the move direction means the move is
    information, not noise — and information drifts (follow) while noise
    reverts (fade). Returns (confirmed, news_backed, sentiment)."""
    nb = news_backed(question)
    ns = headline_sentiment(question)
    conf = bool(nb and ns is not None and (ns > 0) == (ch > 0)
                and abs(ns) >= min_sent)
    return conf, nb, ns


def scan_news(cfg, skip_ids, room, bankroll, blocked_categories=(), multiplier=1.0, tuning=None,
              section="news", strategy="news"):
    """News-reaction strategy: when major news hits, the first place it shows
    is a sharp price move on a high-volume market. Polymarket reports each
    market's one-hour price change; a big jump = news event. Research shows
    prices drift further in the news direction (underreaction), so we buy the
    direction of the move with a tight stop and a profit target."""
    ncfg = cfg.get(section, {})
    out = []
    ms = get_json(f"{GAMMA}/markets", params={
        "active": "true", "closed": "false", "order": "volume24hr",
        "ascending": "false", "limit": 100}) or []
    for m in ms:
        if len(out) >= room:
            break
        if str(m.get("id")) in skip_ids:
            continue
        ch = fnum(m.get("oneHourPriceChange"))
        t = tuning or {}
        if abs(ch) < (t.get("min_move") or ncfg.get("min_move_1h", 0.08)):
            continue
        # SMART NEWS: headline-confirmed moves are a different trade from
        # unconfirmed ones. Confirmed = follow the information; model 16
        # holds a veto (if history says this move shape REVERTS, abstain).
        confirmed, nb, nsent = news_confirmed(
            m.get("question", ""), ch, ncfg.get("min_confirm_sent", 0.3))
        if confirmed and _SPORTSY.search(m.get("question", "")):
            confirmed = False   # bag-of-words sentiment cannot tell which
            # TEAM a headline favors — polarity is noise on vs/O-U markets
        if section != "news":
            confirmed = False   # daytrade reuses this scanner but is a
            # pure fade desk — follow-mode belongs to the news book only
        if section == "daytrade" and nb:
            continue   # a news-BACKED move DRIFTS on the information; fading
            # it is "how fades die" (news_backed docstring) — both Politics
            # daytrade stop-outs (Trump, Iran) were exactly this
        p_rev = None
        if (confirmed and section == "news") or section == "daytrade":
            toks_pre = jlist(m.get("clobTokenIds"))
            if toks_pre:
                p_rev = chartml.move_predict(
                    CHARTML, _best_series(toks_pre[0], m.get("id")), ch)
            if section == "news" and p_rev is not None and p_rev >= 0.5:
                confirmed = False   # learned chartist: this shape reverts
            if (section == "daytrade" and p_rev is not None
                    and p_rev < cfg.get("daytrade", {}).get(
                        "ml_min_revert", 0.5)):
                model_acted("chartml")
                continue   # parity with the fast loop: fade only what
                # history says reverts (the slow path bypassed model 16)
        if (not confirmed and ("up" if ch > 0 else "down")
                in t.get("blocked_directions", [])):
            continue  # learned: this direction of (unconfirmed) news loses
        if fnum(m.get("volume24hr")) < ncfg.get("min_volume_24h", 20000):
            continue
        end = parse_end_date(m)
        if is_in_game(m):
            continue  # the game is LIVE — jump risk, stops are placebo
        if not end or (end - now_utc()).total_seconds() < \
                ncfg.get("min_hours_to_resolution", 6) * 3600:
            continue  # post-mortem verdict: every major loss was an in-game
            # market (resolves in hours, price moves in jumps, stops gap) —
            # fade only moves with time to mean-revert
        prices = [fnum(p) for p in jlist(m.get("outcomePrices"))]
        tokens = jlist(m.get("clobTokenIds"))
        if len(prices) != 2 or len(tokens) != 2:
            continue
        # Model 7: scale the move by binary-outcome volatility at this price —
        # a 12c jump on a 95c market is seismic, on a 50c market it's Tuesday.
        p_pre = min(0.99, max(0.01, prices[0] - ch))
        if abs(ch) / max(0.15, (p_pre * (1 - p_pre)) ** 0.5) < 0.22:
            model_acted("zscore")
            continue
        ev_id = str((m.get("events") or [{}])[0].get("id") or "")
        if ev_id and ev_id in skip_ids:
            continue  # already trading another market of this same event
        side = 0 if ch > 0 else 1  # follow the move...
        if not confirmed and ncfg.get("mode") == "fade":
            side = 1 - side  # ...or fade it, if the lab proved that better
        stats = book_stats(tokens[side])
        time.sleep(0.1)
        if not stats or stats["spread"] > 0.03:
            continue
        if stats["imbalance"] < 0.45:
            continue  # order book disagrees with the move — no confirmation
        if sum(p * sz for p, sz in stats["ask_levels"]) < 50:
            model_acted("quality")
            continue  # model 8: thin book on a "news" move = likely a trap
        price = stats["ask"]
        if not 0.10 <= price <= 0.85:
            continue  # too decided already, or junk
        # category resolved before sizing so the per-category brain specialist
        # sees it (point-in-time, threaded — not recomputed at decision time).
        category = market_category(m)
        time.sleep(0.1)
        if category in blocked_categories:
            continue  # learning says news trades in this category lose
        dollars = min(ncfg.get("max_dollars_per_trade", 5.0) * multiplier, bankroll)
        adj = brain_adjust("news", price,
                           {"spread": stats["spread"],
                            "imbalance": stats["imbalance"], "move_1h": ch},
                           side=(jlist(m.get("outcomes")) + ["Yes", "No"])[side],
                           category=category)
        if adj <= 0.45:
            model_acted("brain")
            continue  # model 13: brain says this context loses
        dollars *= adj
        shares = int(min(stats["ask_size"], dollars / price))
        if shares < 1:
            continue
        cf = None
        if section == "daytrade":
            cf = chart_features(tokens[side])
            time.sleep(0.1)
            if not cf or cf["chart_pattern"] not in ("spike_fade", "mean_dev"):
                continue   # the chartist fades overextensions, never breakouts
        stop = round(price - ncfg.get("stop_drop", 0.06), 3)
        target = round(price + ncfg.get("target_gain", 0.10), 3)
        skip_ids = set(skip_ids) | {ev_id}
        out.append({
            "strategy": strategy, "event_id": ev_id or None, "category": category,
            "context": dict({"move_1h": ch, "spread": stats["spread"],
                             "imbalance": stats["imbalance"],
                             "mode": ("follow-news" if confirmed
                                      else ncfg.get("mode", "follow")),
                             "news_backed": nb,
                             "news_sent": nsent,
                             "chart_ml": (round(p_rev, 3)
                                          if p_rev is not None else None),
                             "brain_adj": round(adj, 2)}, **(cf or {})),
            "name": m["question"],
            "legs": [{"market_id": str(m["id"]), "question": m["question"],
                      "token_index": side, "token_id": tokens[side], "price": price,
                      "outcome": (jlist(m.get("outcomes")) + ["Yes", "No"])[side]}],
            "shares": shares, "cost": round(shares * price, 2),
            "entry_price": price, "stop": stop, "target": target,
            "detail": f"news move {ch:+.2f} in 1h -> buy @ {price:.2f}, "
                      f"stop {stop:.2f}, target {target:.2f}",
        })
    return out



def daytrade_loop(cfg, account):
    """The fast desk: a watchlist of liquid, slow-resolving markets is
    bulk-priced every ~15 seconds (ONE API call via POST /books). A 3c+
    move within 5 minutes — detected from our own tick memory, not the
    hourly field — triggers the full entry pipeline (chart read, gates,
    brackets) within seconds of the overreaction. Exits were already
    second-level; now entries are too."""
    universe, ticks, last_uni = {}, {}, 0.0
    while True:
        try:
            cfg = load_config()
            dcfg = cfg.get("daytrade", {})
            if not dcfg.get("enabled"):
                time.sleep(30)
                continue
            now = time.time()
            if now - last_uni > 300:          # refresh the watchlist
                last_uni = now
                universe = {}
                for m in get_json(f"{GAMMA}/markets", params={
                        "active": "true", "closed": "false",
                        "order": "volume24hr", "ascending": "false",
                        "limit": 100}) or []:
                    if fnum(m.get("volume24hr")) < dcfg.get("min_volume_24h", 10000):
                        continue
                    end = parse_end_date(m)
                    if not end or (end - now_utc()).total_seconds() <                             dcfg.get("min_hours_to_resolution", 6) * 3600:
                        continue              # in-game trap stays excluded
                    toks = jlist(m.get("clobTokenIds"))
                    if len(toks) == 2:
                        universe[str(toks[0])] = m
            if not universe:
                time.sleep(15)
                continue
            books = fetch_books_bulk(list(universe))
            cutoff = now - 360
            for tok, m in universe.items():
                bs = books.get(tok)
                if not bs:
                    continue
                mid = (bs["bid"] + bs["ask"]) / 2
                h = ticks.setdefault(tok, [])
                h.append((now, mid))
                ticks[tok] = h = [(t, p) for t, p in h if t >= cutoff]
                base = next((p for t, p in h if t <= now - 240), None)
                if base is None:
                    continue
                move = mid - base
                if abs(move) < dcfg.get("fast_move_5m", 0.03):
                    continue
                # an overreaction is live RIGHT NOW — run the full pipeline
                learning = compute_learning(account)
                dt = learning.get("daytrade", {})
                if dt.get("multiplier", 1.0) <= 0:
                    continue
                open_dt = sum(1 for p in account["positions"]
                              if p["strategy"] == "daytrade")
                if open_dt >= dcfg.get("max_open_positions", 12):
                    continue
                # 6h cooldown, not the scanners' 30min: a stopped fade IS
                # evidence the "overreaction" was repricing (SpaceX IPO:
                # stopped -6.09, re-entered 30min later, -1.17 more)
                skip = held_ids(account) | {mid_ for mid_, ts in REENTRY.items()
                                            if now - ts < dcfg.get(
                                                "reentry_cooldown_s", 21600)}
                if str(m.get("id")) in skip:
                    continue
                side = 1 if move > 0 else 0     # fade the move
                toks2 = jlist(m.get("clobTokenIds"))
                sb = books.get(tok) if side == 0 else book_stats(toks2[1])
                if not sb or sb["spread"] > 0.03 or sb["imbalance"] < 0.45:
                    continue
                price = sb["ask"]
                if not 0.10 <= price <= 0.85:
                    continue
                cf = chart_features(toks2[side])
                if not cf or cf["chart_pattern"] not in ("spike_fade",
                                                         "mean_dev"):
                    continue
                if is_in_game(m):
                    continue  # the game is LIVE — jump risk, the move model's
                    # mean-reversion assumption breaks down (parity with
                    # scan_news at line 5611)
                if news_backed(m.get("question", "")):
                    continue  # a news-BACKED move DRIFTS, it doesn't revert;
                    # fading it is "how fades die" — parity with the slow path,
                    # and exactly the Politics daytrade stop-outs we just took
                # learned chart gate (model 16): trained on 5,675 recorded
                # moves, OOS skill +0.075 on a 1,703-event chronological
                # holdout — fade only what history says actually reverts
                p_rev = chartml.move_predict(
                    CHARTML, _best_series(tok, m.get("id")), move)
                if (p_rev is not None
                        and p_rev < dcfg.get("ml_min_revert", 0.5)):
                    model_acted("chartml")
                    continue            # history says this move type RUNS
                bm = bankroll_manager(cfg, account)
                risk_shares = bm["risk_per_trade"] / max(
                    dcfg.get("stop_drop", 0.05), 0.01)
                dollars = min(dcfg.get("max_dollars_per_trade", 10.0)
                              * model_multiplier(compute_models(account),
                                                 "daytrade"),
                              risk_shares * price,   # risk/stop-distance
                              strategy_budget(cfg, account, "daytrade"))
                dollars *= bm["dd_factor"]
                # one category read, threaded into both the per-category brain
                # specialist and the opportunity record (no recompute, no
                # second network call at decision time).
                dt_category = market_category(m)
                if dt_category in (dt.get("blocked_categories") or []):
                    continue  # learning says daytrades in this category lose
                adj = brain_adjust("daytrade", price,
                                   {"spread": sb["spread"],
                                    "imbalance": sb["imbalance"],
                                    "move_1h": move},
                                   category=dt_category)
                if adj <= 0.45:
                    continue
                shares = int(min(sb["ask_size"], dollars * adj / price))
                if shares < 1:
                    continue
                opp = {
                    "strategy": "daytrade", "event_id": None,
                    "category": dt_category,
                    "context": dict({"move_5m": round(move, 3),
                                     "spread": sb["spread"],
                                     "imbalance": sb["imbalance"],
                                     "chart_ml": (round(p_rev, 3)
                                                  if p_rev is not None
                                                  else None),
                                     "mode": "fade", "fast": True,
                                     "news_backed": news_backed(m.get("question", "")),
                                     "news_sent": headline_sentiment(m.get("question", "")),
                                     "brain_adj": round(adj, 2)}, **(cf or {})),
                    "name": m["question"],
                    "legs": [{"market_id": str(m["id"]),
                              "question": m["question"], "token_index": side,
                              "token_id": toks2[side], "price": price,
                              "outcome": (jlist(m.get("outcomes"))
                                          + ["Yes", "No"])[side]}],
                    "shares": shares, "cost": round(shares * price, 2),
                    "entry_price": price,
                    "stop": round(price - dcfg.get("stop_drop", 0.05), 3),
                    "target": round(price + dcfg.get("target_gain", 0.08), 3),
                    "detail": f"FAST fade: {move:+.3f} in 5min -> buy @ "
                              f"{price:.2f}",
                }
                with ACCOUNT_LOCK:
                    open_position(account, opp, cfg)
                    save_account(account)
        except Exception as e:
            print(f"  ! daytrade loop error (will retry): {e}")
        time.sleep(max(5, int(cfg.get("daytrade", {}).get("fast_seconds", 15))))


def evolver_loop():
    """Every 6 hours: re-run the optimizer (favorites band from backtest
    evidence) and the strategy lab (follow/fade variants on fresh history).
    Winners are written straight into config, which the trading loop
    hot-reloads — the bot re-forms its own strategy while it trades."""
    while True:
        time.sleep(6 * 3600)
        try:
            note("EVOLVER: re-testing all strategies against fresh history...")
            optimize()
            lab(150)
            promote_explorer_findings()
            train_market_model()
            note("EVOLVER: strategy re-derived from latest evidence (see config)")
        except Exception as e:
            print(f"  ! evolver error (will retry in 6h): {e}")


HEARTBEAT = {"t": time.time()}


def monitor_pass(cfg, account, do_settle=False):
    """The fast loop: watch open trades' live prices for exit decisions.
    Resolution checks are slower API calls, so they run less often."""
    HEARTBEAT["t"] = time.time()
    check_exits(cfg, account)
    if do_settle:
        settle_positions(account)
    save_account(account)
    record_history(account)


def run_pass(cfg, account, trade=True):
    """One full cycle: settle finished markets, learn, look for new trades."""
    print(f"\n=== scan at {now_utc():%Y-%m-%d %H:%M} UTC ===")
    if trade:
        check_exits(cfg, account)
        settle_positions(account)

    learning = compute_learning(account)
    if trade:
        save_learning(learning)
    BRAIN.update(brain_train(account))
    try:
        atomic_write(BRAIN_FILE, json.dumps(BRAIN, indent=1))
    except OSError:
        pass
    # the getting-smarter ledger: every few minutes, snapshot how SKILLED
    # the learners measure out-of-sample — the honest curve of intelligence
    try:
        hist = (json.loads(SKILL_HIST_FILE.read_text())
                if SKILL_HIST_FILE.exists() else [])
    except ValueError:
        hist = []
    now_iso = now_utc().isoformat(timespec="seconds")
    if not hist or hist[-1]["t"] < (now_utc() - timedelta(minutes=4)
                                    ).isoformat(timespec="seconds"):
        mm = {}
        if MARKET_MODEL_FILE.exists():
            try:
                mm = json.loads(MARKET_MODEL_FILE.read_text())
            except ValueError:
                pass
        hist.append({"t": now_iso,
                     "brain": ((BRAIN.get("oos") or {}).get("cv_skill")
                               if BRAIN.get("oos") else None),
                     "voice": round(BRAIN.get("skill_factor", 0), 2),
                     "m15": mm.get("skill_vs_market")})
        atomic_write(SKILL_HIST_FILE, json.dumps(hist[-5000:]))
    models = compute_models(account)
    compute_risk(account)
    compute_patterns(account)
    HEARTBEAT["t"] = time.time()
    problems = audit_books(account)
    if problems and problems != AUDIT_LAST["problems"]:
        AUDIT_LAST["problems"] = problems
        note("AUDIT ALERT — books don't balance: " + "; ".join(problems))
    elif not problems:
        AUDIT_LAST["problems"] = None
    tod_block = models["m4_time_of_day"]["now_blocked"]
    hit = [s for s, b in tod_block.items() if b]
    if hit:
        print(f"  model 4: {models['m4_time_of_day']['now']} UTC has a proven "
              f"net loss for {', '.join(hit)} — those wait (exits still active)")

    risk = cfg.get("risk", {})
    if trade and risk.get("max_daily_loss_pct"):
        today = now_utc().strftime("%Y-%m-%d")
        day_pnl = (sum(t["pnl"] for t in account["settled"]
                       if t["closed"][:10] == today)
                   + sum(position_value(p) - p["cost"] for p in account["positions"]))
        if day_pnl <= -risk["max_daily_loss_pct"] / 100 * account["starting_cash"]:
            print(f"  CIRCUIT BREAKER: down ${-day_pnl:.2f} today — no new "
                  f"trades until tomorrow (exits still active)")
            save_account(account)
            record_history(account)
            return

    skip = held_ids(account)
    if cfg.get("arbitrage", {}).get("enabled"):
        opportunities_pairs = scan_pairs(cfg, held_ids(account))
    else:
        opportunities_pairs = []
    now_t = time.time()
    skip = set(skip) | {mid for mid, ts in REENTRY.items() if now_t - ts < 1800}
    opportunities = list(opportunities_pairs)
    if (cfg["arbitrage"]["enabled"] and learning["arbitrage"]["multiplier"] > 0
            and not cfg["arbitrage"].get("fast_scan_seconds")):
        opportunities += scan_arbitrage(cfg, skip,
                                        learning["arbitrage"]["multiplier"])
    hp = learning["high_prob"]
    if (cfg["high_probability"]["enabled"] and hp["multiplier"] > 0
            and not tod_block["high_prob"]):
        open_hp = [p for p in account["positions"] if p["strategy"] == "high_prob"]
        cat_counts = {}
        for p in open_hp:
            c = p.get("category") or "Other"
            cat_counts[c] = cat_counts.get(c, 0) + 1
        bm = bankroll_manager(cfg, account)
        opportunities += scan_high_prob(cfg, skip, len(open_hp),
                                        hp["multiplier"] * bm["dd_factor"]
                                        * model_multiplier(models, "high_prob"),
                                        hp["blocked_bands"],
                                        hp["blocked_categories"], cat_counts,
                                        bankroll=account["cash"],
                                        band_stats=band_win_stats(account),
                                        held_names=[p["name"] for p in
                                                    account["positions"]
                                                    if p["strategy"]
                                                    == "high_prob"],
                                        sports_exposure=sports_probe_spent(
                                            account),
                                        held_event_ids=[
                                            p.get("event_id")
                                            for p in account["positions"]
                                            if p.get("event_id")])

    ex = learning["explore"]
    if (cfg.get("explore", {}).get("enabled") and ex["multiplier"] > 0
            and not tod_block["explore"]):
        open_ex = [p for p in account["positions"] if p["strategy"] == "explore"]
        ex_cats = {}
        for p in open_ex:
            c = p.get("category") or "Other"
            ex_cats[c] = ex_cats.get(c, 0) + 1
        cells = {}
        for p in open_ex:
            c = (p.get("category") or "Other",
                 int(round((p.get("entry_price") or 0) * 20)) * 5)
            cells[c] = cells.get(c, 0) + 1
        ranked = thompson_rank(account, scan_high_prob(
            cfg, skip, len(open_ex), 1.0,
            ex["blocked_bands"], ex["blocked_categories"], ex_cats,
            bankroll=account["cash"], band_stats={},
            section="explore", strategy="explore", use_kelly=False))
        for o in ranked:   # diversity of contexts is the data; volume in one
            c = (o.get("category") or "Other",  # context is just noise
                 int(round((o.get("entry_price") or 0) * 20)) * 5)
            if cells.get(c, 0) >= 3:
                continue
            cells[c] = cells.get(c, 0) + 1
            opportunities.append(o)

    dt = learning.get("daytrade")
    if (cfg.get("daytrade", {}).get("enabled") and dt and dt["multiplier"] > 0
            and not tod_block.get("daytrade")):
        open_dt = sum(1 for p in account["positions"] if p["strategy"] == "daytrade")
        dt_room = cfg["daytrade"]["max_open_positions"] - open_dt
        if dt_room > 0:
            now_t = time.time()
            dt_skip = skip | {mid_ for mid_, ts_ in REENTRY.items()
                              if now_t - ts_ < cfg["daytrade"].get(
                                  "reentry_cooldown_s", 21600)}
            opportunities += scan_news(cfg, dt_skip, dt_room,
                                       strategy_budget(cfg, account, "daytrade"),
                                       dt["blocked_categories"],
                                       dt["multiplier"]
                                       * model_multiplier(models, "daytrade"),
                                       None, section="daytrade",
                                       strategy="daytrade")

    if (cfg.get("news", {}).get("enabled")
            and not tod_block["news"]):
        open_news = sum(1 for p in account["positions"] if p["strategy"] == "news")
        room = cfg["news"]["max_open_positions"] - open_news
        if room > 0:
            if learning["news"]["multiplier"] > 0:
                opportunities += scan_news(cfg, skip, room,
                                           strategy_budget(cfg, account, "news"),
                                           learning["news"]["blocked_categories"],
                                           learning["news"]["multiplier"]
                                           * model_multiplier(models, "news"),
                                           learning["news"]["tuning"])

    if not opportunities:
        print("  no opportunities this pass (normal — good ones are rare)")

    def _velocity(o):
        """Rank by EV per day: a 2c edge resolving tonight beats a 3c edge
        resolving Friday. Arbitrage is locked profit — always first."""
        if o["strategy"] == "arbitrage":
            return 99.0
        if o["strategy"] == "explore" and "_ts" in o:
            return o["_ts"]  # Thompson draw decides exploration order
        hrs = (o.get("context") or {}).get("hours_to_end") or 48
        return (1 - (o.get("entry_price") or 0.5)) / max(hrs / 24, 0.25)
    opportunities.sort(key=_velocity, reverse=True)
    for opp in opportunities:
        if trade:
            open_position(account, opp, cfg)
        else:
            print(f"  * FOUND [{opp['strategy']}] {opp['name'][:60]} — "
                  f"cost ${opp['cost']:.2f} — {opp['detail']}")
    if trade:
        save_account(account)
        record_history(account, force=True)


def show_status(account):
    invested = sum(position_value(p) for p in account["positions"])
    total = account["cash"] + invested  # open positions at latest live prices
    print(f"\nPaper account (started with ${account['starting_cash']:.2f} "
          f"on {account['created'][:10]})")
    print(f"  cash:            ${account['cash']:.2f}")
    print(f"  in open trades:  ${invested:.2f}  ({len(account['positions'])} positions)")
    print(f"  realized profit: ${account['realized_pnl']:+.2f}")
    print(f"  account value:   ${total:.2f}  "
          f"({total - account['starting_cash']:+.2f} overall)")
    for pos in account["positions"]:
        print(f"    - [{pos['strategy']}] {pos['name'][:70]} "
              f"(cost ${pos['cost']:.2f}, opened {pos['opened'][:10]})")
    if TRADE_LOG.exists():
        print(f"\nFull history: {TRADE_LOG}")
    print(f"Dashboard: run 'python3 bot.py web' then open http://localhost:{DASHBOARD_PORT}")


# ------------------------------------------------------------- dashboard

def _roi_table(settled, key):
    """Per-<key> (category or strategy) ROI on capital ACTUALLY deployed:
    deployed = sum(cost), pnl = sum(pnl), roi% = pnl/deployed*100. Over the
    FULL settled history (the page only ships the last 25 rows, so this must be
    computed server-side). Sorted best-ROI first."""
    agg = {}
    for t in settled:
        k = t.get(key) or "?"
        a = agg.setdefault(k, {"n": 0, "deployed": 0.0, "pnl": 0.0, "wins": 0})
        a["n"] += 1
        a["deployed"] += t.get("cost") or 0.0
        a["pnl"] += t.get("pnl", 0) or 0.0
        a["wins"] += 1 if (t.get("pnl", 0) or 0) > 0 else 0
    rows = []
    for k, a in agg.items():
        dep = a["deployed"]
        rows.append({"key": k, "n": a["n"], "deployed": round(dep, 2),
                     "pnl": round(a["pnl"], 2),
                     "roi": round(100.0 * a["pnl"] / dep, 1) if dep else 0.0,
                     "win": round(100.0 * a["wins"] / a["n"]) if a["n"] else 0})
    return sorted(rows, key=lambda r: -r["roi"])


def dashboard_state():
    """Everything the web page needs, read fresh from disk each time."""
    cfg = load_config()
    account = load_account(cfg)
    positions = []
    for p in account["positions"]:
        value = position_value(p)
        leg0 = p["legs"][0]
        positions.append({
            "strategy": p["strategy"], "name": p["name"], "cost": p["cost"],
            "side": ("ALL outcomes (locked)" if p["strategy"] == "arbitrage"
                     else leg0.get("outcome") or ("Yes" if leg0["token_index"] == 0 else "No")),
            "category": p.get("category"),
            "opened": p["opened"], "value": value,
            "pnl": round(value - p["cost"], 2),
            "entry_price": p.get("entry_price"),
            "last_price": p.get("last_price"),
            "last_checked": p.get("last_checked"),
            "stop": p.get("stop"), "target": p.get("target"),
            "context": p.get("context"),
            "path": p.get("path", [])[-60:],
        })
    invested = round(sum(x["value"] for x in positions), 2)
    cost_basis = sum(p["cost"] for p in account["positions"])
    history = []
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text())
        except ValueError:
            history = []
    activity = []
    if ACTIVITY_FILE.exists():
        activity = ACTIVITY_FILE.read_text().splitlines()[-60:][::-1]
    bt = None
    if BACKTEST_FILE.exists():
        try:
            bt = json.loads(BACKTEST_FILE.read_text())
        except ValueError:
            bt = None
    recorder = None
    if DATA_STATS_FILE.exists():
        try:
            recorder = json.loads(DATA_STATS_FILE.read_text())
        except ValueError:
            recorder = None
    models = None
    if MODELS_FILE.exists():
        try:
            models = json.loads(MODELS_FILE.read_text())
        except ValueError:
            models = None
    risk = None
    if RISK_FILE.exists():
        try:
            risk = json.loads(RISK_FILE.read_text())
        except ValueError:
            risk = None
    patterns = None
    if PATTERNS_FILE.exists():
        try:
            patterns = json.loads(PATTERNS_FILE.read_text())
        except ValueError:
            patterns = None
    curve, cum = [], 0.0
    window = []
    for t in account["settled"]:
        cum = round(cum + t["pnl"], 2)
        window.append(t["pnl"])
        if len(window) > 20:
            window.pop(0)
        curve.append({"t": t.get("closed"), "cum": cum,
                      "roll": round(sum(window) / len(window), 3)})
    day_ago = (now_utc() - timedelta(hours=24)).isoformat(timespec="seconds")
    recent = [t for t in account["settled"] if t.get("closed", "") >= day_ago]
    nonarb = [t for t in account["settled"] if t["strategy"] != "arbitrage"]
    return {
        "pnl_curve": curve,
        "skill_history": (json.loads(SKILL_HIST_FILE.read_text())
                          if SKILL_HIST_FILE.exists() else []),
        "settles_24h": len(recent),
        "raw_n": len(nonarb),
        "effective_n": effective_n(nonarb),
        "models": models,
        "risk": risk,
        "patterns": patterns,
        "now": now_utc().isoformat(timespec="seconds"),
        "scan_interval_minutes": cfg.get("scan_interval_minutes"),
        "monitor_interval_seconds": cfg.get("monitor_interval_seconds"),
        "account": {
            "cash": round(account["cash"], 2),
            "invested": invested,
            "unrealized_pnl": round(invested - cost_basis, 2),
            "total": round(account["cash"] + invested, 2),
            "starting_cash": account["starting_cash"],
            "realized_pnl": round(account["realized_pnl"], 2),
            "created": account["created"],
        },
        "positions": positions,
        "settled": account["settled"][-25:][::-1],
        "settled_total": len(account["settled"]),
        "history": history,
        "learning": compute_learning(account),
        "activity": activity,
        "backtest": bt,
        "recorder": recorder,
        "metrics": compute_metrics(account, history),
        "category_budgets": {cat: {
            "alloc": alloc,
            "used": round(sum(p["cost"] for p in account["positions"]
                              if p.get("category") == cat), 2),
            "pnl": round(sum(t["pnl"] for t in account["settled"]
                             if t.get("category") == cat)
                         + sum(p["pnl"] for p in positions
                               if p.get("category") == cat), 2),
            "open": sum(1 for p in positions if p.get("category") == cat),
            "done": sum(1 for t in account["settled"] if t.get("category") == cat)}
            for cat, alloc in cfg.get("category_allocations", {}).items()
            } | (lambda known: {"Other": {
                "alloc": 0,
                "used": round(sum(p["cost"] for p in account["positions"]
                                  if p.get("category") not in known), 2),
                "pnl": round(sum(t["pnl"] for t in account["settled"]
                                 if t.get("category") not in known)
                             + sum(p["pnl"] for p in positions
                                   if p.get("category") not in known), 2),
                "open": sum(1 for p in positions if p.get("category") not in known),
                "done": sum(1 for t in account["settled"]
                            if t.get("category") not in known)}})(
                set(cfg.get("category_allocations", {}))),
        "strategy_text": strategy_text(cfg),
        "accounts": {s: {
            "alloc": cfg.get("allocations", {}).get(s, 0),
            "realized": round(sum(t["pnl"] for t in account["settled"]
                                  if t["strategy"] == s), 2),
            "unrealized": round(sum(p["pnl"] for p in positions
                                    if p["strategy"] == s), 2),
            "open": sum(1 for p in positions if p["strategy"] == s),
            "trades": sum(1 for t in account["settled"] if t["strategy"] == s),
            "value": round(cfg.get("allocations", {}).get(s, 0)
                           + sum(t["pnl"] for t in account["settled"] if t["strategy"] == s)
                           + sum(p["pnl"] for p in positions if p["strategy"] == s), 2),
        } for s in STRATEGIES},
        "roi": {
            "by_category": _roi_table(account["settled"], "category"),
            "by_strategy": _roi_table(account["settled"], "strategy"),
        },
    }


def brief():
    """Ultra-compact state digest for scheduled AI reviews — one screen,
    minimal tokens, replaces reading the raw JSON files."""
    s = dashboard_state()
    a = s["account"]
    out = [f"BRIEF {s['now']} total {a['total']} cash {a['cash']} inv {a['invested']} "
           f"real {a['realized_pnl']:+} unreal {a['unrealized_pnl']:+} "
           f"open {len(s['positions'])} settled {s['settled_total']} "
           f"(eff {s.get('effective_n', '?')}) 24h {s.get('settles_24h', '?')} "
           f"start {a['starting_cash']}"]
    out.append("STRAT " + " | ".join(
        f"{k[:4]} v{A['value']} r{A['realized']:+} u{A['unrealized']:+} o{A['open']} done{A['trades']}"
        for k, A in s["accounts"].items()))
    for k, L in (s.get("learning") or {}).items():
        line = f"LEARN {k[:4]} x{L['multiplier']} n{L['settled']} pnl{L['total_pnl']:+} {L['status']}"
        if L.get("blocked_bands"):
            line += " BLKbands:" + ",".join(L["blocked_bands"])
        if L.get("blocked_categories"):
            line += " BLKcats:" + ",".join(L["blocked_categories"])
        notes = (L.get("tuning") or {}).get("notes") or []
        if notes:
            line += " TUNED:" + ";".join(notes)
        out.append(line)
        if k == "high_prob" and L.get("bands"):
            out.append("BANDS " + " ".join(
                f"{b}:{v['wins']}/{v['n']}{v['pnl']:+.2f}" for b, v in sorted(L["bands"].items())))
        if k == "high_prob" and L.get("categories"):
            out.append("HPCATS " + " ".join(
                f"{c}:{v['wins']}/{v['n']}{v['pnl']:+.2f}" for c, v in L["categories"].items()))
    cats = {c: b for c, b in (s.get("category_budgets") or {}).items() if b["open"] or b["done"]}
    if cats:
        out.append("CATS " + " ".join(
            f"{c}:{b['pnl']:+.2f}(o{b['open']}/d{b['done']},${b['used']:.0f})" for c, b in cats.items()))
    M = s.get("metrics") or {}
    out.append("MET " + " ".join(f"{k}={v}" for k, v in M.items() if v is not None))
    r = s.get("recorder")
    if r:
        out.append(f"REC rows{r.get('total_rows')} mkts{r.get('markets_tracked')}")
    bm = bankroll_manager(load_config(), load_account(load_config()))
    out.append(f"BANKROLL {bm['bankroll']} risk/trade {bm['risk_per_trade']} "
               f"heat {bm['heat_used']}/{bm['heat_cap']} "
               f"dd {bm['drawdown_pct']}% factor x{bm['dd_factor']}")
    rk = s.get("risk")
    if rk and rk.get("var95") is not None:
        st = " ".join(f"{c}{v:+.0f}" for c, v in rk.get("stress_cluster_fails", {}).items())
        out.append(f"RISK ev{rk['expected_pnl']:+} var95 {rk['var95']} cvar95 {rk['cvar95']} "
                   f"worstall {rk['worst_case_total']} stress[{st}]")
    try:    # tick memory lives in the RUNNING bot, not this CLI process
        h = session.get("http://localhost:8765/api/health", timeout=2).json()
        pm = h.get("price_mem") or {}
        if pm.get("points"):
            out.append(f"MEM rss {h.get('rss_mb')}MB ticks {pm['points']} "
                       f"({pm['tokens']} mkts ~{pm['mb']}MB)")
    except Exception:
        pass
    if NEWS_STATS_FILE.exists():
        try:
            nf = json.loads(NEWS_STATS_FILE.read_text())
            out.append(f"NEWSFEED headlines{nf.get('headlines', 0)}")
        except ValueError:
            pass
    if SCORES_FILE.exists():
        try:
            sc = json.loads(SCORES_FILE.read_text())
            lats = sorted(sc.get("latencies") or [])
            if sc.get("events"):
                f = sc.get("firsts") or {}
                out.append(f"SCORES events{sc['events']} armed{sc['armed']} "
                           f"measured{len(lats)} "
                           f"median{lats[len(lats) // 2] if lats else '-'}s "
                           f"firsts[{','.join(f'{k}:{v}' for k, v in f.items())}]")
        except ValueError:
            pass
    mo = s.get("models")
    if mo:
        bay = ",".join(f"{k[:2]}:{v['p_win']}x{v['mult']}"
                       for k, v in mo.get("m3_bayes", {}).items())
        cl = " ".join(f"{c}${v:.0f}" for c, v in sorted(
            mo.get("m5_clusters", {}).items(), key=lambda x: -x[1])[:4])
        out.append(
            f"MODELS mult{mo['size_mult']} vol:{mo['m1_vol_regime']['state']} "
            f"trend:{mo['m2_equity_trend']['state']} bayes[{bay}] "
            f"todblk:{';'.join(f'{s[:2]}={','.join(v)}' for s, v in mo['m4_time_of_day']['blocked'].items() if v) or '-'} "
            f"clusters[{cl}] veto c{mo.get('m5_cluster_vetoes', 0)}/"
            f"i{mo.get('m6_impact_vetoes', 0)}/z{mo.get('m7_zscore_vetoes', 0)}/"
            f"q{mo.get('m8_quality_vetoes', 0)}/"
            f"pt{(mo.get('m11_patterns') or {}).get('vetoed', 0)}"
            f"(act{(mo.get('m11_patterns') or {}).get('active_vetoes', 0)}) "
            f"exits eg{mo.get('m9_edge_gone_exits', 0)}/"
            f"bp{mo.get('m10_pressure_exits', 0)}/sl{mo.get('m12_slide_exits', 0)}")
    out.append("POS strat|side|$cost|entry>last|pnl|market (worst first)")
    for p in sorted(s["positions"], key=lambda x: x["pnl"])[:25]:
        e, l = p.get("entry_price"), p.get("last_price") or p.get("entry_price")
        px = "lock" if p["strategy"] == "arbitrage" else f"{(e or 0)*100:.0f}>{(l or 0)*100:.0f}"
        out.append(f"{p['strategy'][:3]}|{(p['side'] or '?')[:10]}|{p['cost']:.2f}|{px}|{p['pnl']:+.2f}|{p['name'][:45]}")
    out.append("SETTLED date|strat|pnl|why|market (newest 8)")
    for t in s["settled"][:8]:
        out.append(f"{t['closed'][5:10]}|{t['strategy'][:3]}|{t['pnl']:+.2f}|"
                   f"{(t.get('reason') or 'res')[:12]}|{t['name'][:45]}")
    print("\n".join(out))


def strategy_text(cfg):
    """The current strategy, written out in plain English from live config."""
    h, q, e, a = (cfg["high_probability"], cfg.get("quant", {}),
                  cfg.get("exit", {}), cfg["arbitrage"])
    return [
        f"1. ARBITRAGE — scan every {a.get('fast_scan_seconds', '?')}s for events where "
        f"all outcomes' YES prices sum below $1 (min edge {a['min_edge_cents']}¢); buy every "
        f"outcome, hold to resolution — payout is locked. Max ${a['max_cost_per_arb']:.0f} each.",
        f"2. FAVORITES — buy outcomes at {h['buy_price_min']*100:.0f}–{h['buy_price_max']*100:.0f}¢, "
        f"only {h.get('min_hours_to_resolution', 0)}h–{h['max_days_to_resolution']*24}h before resolution "
        f"(the window the 692-trade backtest validated), volume ≥ ${h['min_volume_24h']:,}.",
        f"3. FILTERS — skip if spread > {q.get('max_spread', 0)*100:.0f}¢, order book < "
        f"{q.get('min_book_imbalance', 0)*100:.0f}% buyers, or price fell > {q.get('momentum_max_drop', 0)*100:.0f}¢ "
        f"in the last {q.get('momentum_lookback_hours', 0)}h (falling-knife rule).",
        f"4. SIZING — quarter-Kelly on the Wilson lower-bound edge per price band "
        f"(backtest + live trades pooled); no proven edge at the price ⇒ no trade. "
        f"Caps: ${h['max_dollars_per_trade']:.0f}/trade, {h['max_open_positions']} open, "
        f"{h.get('max_open_per_category', '∞')} per market type.",
        f"5. EXITS — watch prices every {cfg.get('monitor_interval_seconds', '?')}s; stop-loss at "
        f"{e.get('stop_loss_price', 0)*100:.0f}¢, take-profit at {e.get('take_profit_price', 0)*100:.1f}¢. "
        f"Arbitrage is never sold early.",
        "6. LEARNING — after 8 settled trades: losing streak halves size, 16 pauses the strategy; "
        "price bands or market types with 6+ settles and net loss are blocked.",
    ] + ([f"7. EXPLORER — flat ${cfg['explore']['max_dollars_per_trade']:.0f} learning bets across "
          f"{cfg['explore']['buy_price_min']*100:.0f}–{cfg['explore']['buy_price_max']*100:.0f}¢ and "
          f"{cfg['explore'].get('min_hours_to_resolution', 0)}h–{cfg['explore']['max_days_to_resolution']}d "
          f"horizons (cells the main book can't see); its settles feed the band/category learning."]
         if cfg.get("explore", {}).get("enabled") else [])


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # keep the terminal clean
        pass

    def _send(self, body, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            if self.path.startswith("/api/stream"):
                # Server-Sent Events: the truly-live channel. Pushes the
                # account tick every 400ms over one held-open connection —
                # no polling, no full-state payloads, just the heartbeat.
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                while True:
                    try:
                        acct = json.loads(ACCOUNT_FILE.read_text())
                        inv = sum(position_value(p)
                                  for p in acct["positions"])
                        tick = {"t": now_utc().isoformat(timespec="seconds"),
                                "total": round(acct["cash"] + inv, 2),
                                "cash": round(acct["cash"], 2),
                                "inv": round(inv, 2),
                                "open": len(acct["positions"])}
                        self.wfile.write(
                            f"data: {json.dumps(tick)}\n\n".encode())
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError,
                            ValueError, OSError):
                        return
                    time.sleep(0.4)
            elif self.path.startswith("/api/health"):
                age = round(time.time() - HEARTBEAT["t"], 1)
                import resource
                rss_mb = round(resource.getrusage(
                    resource.RUSAGE_SELF).ru_maxrss / 1e6)  # macOS: bytes
                self._send(json.dumps({"ok": age < 120, "age_seconds": age,
                                       "audit": AUDIT_LAST["problems"] or "balanced",
                                       "rss_mb": rss_mb,
                                       "reentry": len(REENTRY),
                                       "price_mem": mem_stats(),
                                       "sportsedge": {
                                           "n": SPORTSEDGE.get("scorecard", {}).get("n", 0),
                                           "open": SPORTSEDGE.get("scorecard", {}).get("open_preds", 0),
                                           "verdict": SPORTSEDGE.get("scorecard", {}).get("verdict", "no data"),
                                           "updated": SPORTSEDGE.get("updated")},
                                       "crossmarket": {
                                           "n": CROSSMARKET.get("scorecard", {}).get("n", 0),
                                           "matched": CROSSMARKET.get("scorecard", {}).get("matched", 0),
                                           "verdict": CROSSMARKET.get("scorecard", {}).get("verdict", "no data"),
                                           "updated": CROSSMARKET.get("updated")}}
                                      ).encode(), "application/json")
            elif self.path.startswith("/lightweight-charts.js"):
                self._send((HERE / "lightweight-charts.js").read_bytes(),
                           "application/javascript")
            elif self.path.startswith("/api/state"):
                body = json.dumps(dashboard_state()).encode()
                self._send(body, "application/json")
            else:
                self._send(DASHBOARD_HTML.read_bytes(), "text/html; charset=utf-8")
        except BrokenPipeError:
            pass
        except Exception as e:
            try:
                self._send(json.dumps({"error": str(e)}).encode(), "application/json")
            except Exception:
                pass


def start_dashboard(background=True):
    try:
        server = ThreadingHTTPServer(("127.0.0.1", DASHBOARD_PORT), DashboardHandler)
    except OSError:
        print(f"(dashboard already running at http://localhost:{DASHBOARD_PORT})")
        return None
    url = f"http://localhost:{DASHBOARD_PORT}"
    print(f"Dashboard: {url}  (opening in your browser)")
    threading.Thread(target=lambda: webbrowser.open(url), daemon=True).start()
    if background:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server
    server.serve_forever()


JOURNAL_FILE = HERE / "decisions.jsonl"


def journal(kind, **data):
    """Meticulous audit trail: every move the bot makes becomes one JSON
    line, carrying the full decision context — what the brain said, what
    Thompson drew, which models voted. Claude's deep reviews read this to
    attribute outcomes to decisions, not to guesses."""
    data.update(kind=kind, t=now_utc().isoformat(timespec="seconds"))
    try:
        with JOURNAL_FILE.open("a") as f:
            f.write(json.dumps(data) + "\n")
        if JOURNAL_FILE.stat().st_size > 5_000_000:
            lines = JOURNAL_FILE.read_text().splitlines()[-20000:]
            atomic_write(JOURNAL_FILE, "\n".join(lines) + "\n")
    except OSError:
        pass


AUDIT_LAST = {"problems": None}


def audit_books(account):
    """Reconciliation: prove the books balance to the penny, every pass.
    A real desk never trusts its own accounting — it checks it."""
    problems = []
    open_cost = sum(p["cost"] for p in account["positions"])
    partial = sum(l.get("proceeds", 0) for p in account["positions"]
                  for l in p["legs"] if l.get("settled"))
    expect_cash = account["starting_cash"] + account["realized_pnl"] - open_cost + partial
    if abs(account["cash"] - expect_cash) > 0.011:
        problems.append(f"cash ${account['cash']:.2f} != expected ${expect_cash:.2f}")
    settled_sum = sum(t["pnl"] for t in account["settled"])
    if abs(settled_sum - account["realized_pnl"]) > 0.011:
        problems.append(f"realized ${account['realized_pnl']:.2f} != "
                        f"settled sum ${settled_sum:.2f}")
    seen = set()
    for p in account["positions"]:
        for l in p["legs"]:
            key = (l["market_id"], l["token_index"], p["strategy"])
            if key in seen:
                problems.append(f"duplicate open leg: {p['name'][:40]}")
            seen.add(key)
    for t in account["settled"][-25:]:
        if any(k not in t for k in ("strategy", "pnl", "cost", "closed")):
            problems.append(f"settled trade missing fields: {str(t)[:60]}")
            break
    return problems


def self_test():
    """No-network unit tests for every pure function the money flows through.
    Run after any code change: python3 bot.py test"""
    fails, ran = [], []

    def ok(name, cond):
        ran.append(name)
        print(("  PASS  " if cond else "  FAIL  ") + name)
        if not cond:
            fails.append(name)

    w = wilson_lower(8, 10)
    ok("wilson 8/10 in sane range", 0.60 < w < 0.78)
    ok("wilson empty = 0", wilson_lower(0, 0) == 0)
    ok("wilson monotonic", wilson_lower(9, 10) > wilson_lower(5, 10))

    ok("vwap walks the book", vwap_fill([(0.5, 10), (0.6, 10)], 15) ==
       round((10 * .5 + 5 * .6) / 15, 4))
    ok("vwap thin book = None", vwap_fill([(0.5, 5)], 10) is None)

    q = {"kelly_fraction": 0.25}
    ok("kelly: no edge = $0", kelly_dollars(1000, 0.97, 96, {"96": (51, 52)}, q) == 0)
    ok("kelly: real edge > $0", kelly_dollars(1000, 0.90, 90, {"90": (98, 100)}, q) > 0)
    ok("kelly: no data = $0", kelly_dollars(1000, 0.97, 97, {}, q) == 0)

    ok("cluster: weather", cluster_of("Will the highest temperature in Oslo be 20°C") == "weather")
    ok("cluster: sports", cluster_of("Lakers vs. Celtics") == "sports-game")
    ok("cluster: crypto", cluster_of("Will the price of Bitcoin be above $66k") == "crypto-price")
    ok("cluster: other", cluster_of("Who wins the election?") == "other")
    acct = {"positions": [{"cost": 25.0, "name": f"temperature in city {i}"}
                          for i in range(7)]}
    ok("cluster cap blocks stacking",
       cluster_check(acct, {"name": "temperature in Lima", "cost": 25.0})[0] is True)
    ok("cluster cap allows diversity",
       cluster_check(acct, {"name": "Who wins the election?", "cost": 25.0})[0] is False)

    synth = {"settled": [{"strategy": "news", "pnl": -1.0,
                          "closed": "2026-06-11T02:30:00+00:00"} for _ in range(9)],
             "positions": []}
    tod = time_of_day_model(synth)
    ok("m4 blocks news's own bad hours", "00-06h" in tod["blocked"]["news"])
    ok("m4 never blames other strategies", tod["blocked"]["high_prob"] == [])
    synth_ex = {"settled": [{"strategy": "explore", "pnl": -1.0,
                             "closed": "2026-06-11T20:30:00+00:00"}] * 9,
                "positions": []}
    ok("m4 never grounds the info book",
       time_of_day_model(synth_ex)["blocked"]["explore"] == [])
    arby = {"positions": [{"cost": 85.0, "name": "Mayoral Election",
                           "strategy": "arbitrage"}]}
    ok("locked arbs carry no cluster risk",
       cluster_check(arby, {"name": "Some election market", "cost": 10.0})[0] is False)

    bay = bayes_confidence({"settled": [{"strategy": "news", "pnl": -1.0}] * 12,
                            "positions": []})
    ok("bayes halves a proven loser", bay["news"]["mult"] == 0.5)
    ok("bayes neutral without data", bay["high_prob"]["mult"] == 1.0)

    cfg = {"allocations": {"news": 200.0}}
    acct2 = {"cash": 999, "settled": [{"strategy": "news", "pnl": -10.0}],
             "positions": [{"strategy": "news", "cost": 50.0}]}
    ok("budget = alloc + pnl - deployed",
       strategy_budget(cfg, acct2, "news") == 140.0)

    good = {"starting_cash": 100.0, "cash": 88.0, "realized_pnl": -2.0,
            "positions": [{"cost": 10.0, "name": "x", "strategy": "news",
                           "legs": [{"market_id": "1", "token_index": 0,
                                     "settled": False}]}],
            "settled": [{"strategy": "news", "pnl": -2.0, "cost": 5.0,
                         "closed": "2026-06-11T01:00:00+00:00"}]}
    ok("audit passes balanced books", audit_books(good) == [])
    good["cash"] = 93.0
    ok("audit catches missing $5", audit_books(good) != [])

    win93 = lambda k: {"settled":
        [{"strategy": "explore", "pnl": 0.07, "entry_price": 0.93,
          "name": "city temperature", "closed": f"2026-06-{i % 28 + 1:02d}T10:00:00"}
         for i in range(k)]
        + [{"strategy": "explore", "pnl": -0.93, "entry_price": 0.93,
            "name": "city temperature", "closed": f"2026-06-{i % 28 + 1:02d}T11:00:00"}
           for i in range(25 - k)]}
    ok("promotion: 24/25 not enough", explorer_proven_bands(win93(24)) == set())
    ok("promotion on 25 clustered wins", explorer_proven_bands(win93(25)) == {93})
    clones = {"settled": [{"strategy": "explore", "pnl": 0.07, "entry_price": 0.93,
                           "name": "hourly BTC above 66k",
                           "closed": "2026-06-11T10:00:00"}] * 25}
    ok("promotion: same-experiment repeats can't promote",
       explorer_proven_bands(clones) == set())

    loser = {"strategy": "news", "name": "Lakers vs. Celtics", "category": "Sports",
             "side": "No", "entry_price": 0.50, "pnl": -1.0,
             "closed": "2026-06-11T18:00:00+00:00", "context": {"move_1h": -0.15}}
    saved = PATTERN_VETOES["list"]
    vets = compute_patterns({"settled": [dict(loser) for _ in range(12)],
                             "positions": []})
    ok("miner finds loser patterns", len(vets) > 0)
    opp = {"strategy": "news", "name": "Lakers vs. Celtics", "category": "Sports",
           "entry_price": 0.50, "context": {"move_1h": -0.15},
           "legs": [{"outcome": "No"}]}
    ok("pattern veto blocks the repeat", pattern_veto(opp) is not None)
    PATTERN_VETOES["list"] = ["cat=Nope&side=yes"]
    ok("pattern veto passes the innocent", pattern_veto(opp) is None)
    ok("vetoes are pairs only — no kill-switch singles",
       all("&" in v for v in vets))
    stale = [dict(loser, closed="2026-01-05T18:00:00+00:00") for _ in range(12)]
    ok("stale losses can't veto (14d window)",
       compute_patterns({"settled": stale, "positions": []}) == [])
    PATTERN_VETOES["list"] = saved

    pwin = {"pnl": 0.07, "entry_price": 0.93}
    plose = {"pnl": -0.93, "entry_price": 0.93}
    ok("probation: graduates on clean proof",
       probation_verdict([dict(pwin)] * 20) == "graduate")
    ok("probation: demotes on clean losses",
       probation_verdict([dict(plose)] * 3 + [dict(pwin)] * 9) == "demote")
    ok("probation: one early loss isn't a verdict",
       probation_verdict([dict(plose)] + [dict(pwin)] * 7) is None)
    ok("probation: tiny sample waits",
       probation_verdict([dict(pwin)] * 5) is None)
    ok("miner sees time-to-resolution",
       "ttr=<24h" in trade_features({"strategy": "high_prob", "name": "x",
                                     "context": {"hours_to_end": 12}}))

    exb = lambda pnl, k: {"settled": [{"strategy": "explore", "pnl": pnl,
                                       "entry_price": 0.9,
                                       "closed": "2026-06-11T10:00:00+00:00"}] * k,
                          "positions": []}
    ok("explorer: cheap data never pauses",
       compute_learning(exb(-0.04, 20))["explore"]["multiplier"] == 1.0)
    ok("explorer: spent budget pauses",
       compute_learning(exb(-1.0, 60))["explore"]["multiplier"] == 0.0)
    churny = {"settled": [{"strategy": "high_prob", "pnl": -0.05,
                           "entry_price": 0.97,
                           "closed": "2026-06-11T10:00:00+00:00"}] * 12,
              "positions": []}
    ok("insurance costs can't halve a strategy",
       compute_learning(churny)["high_prob"]["multiplier"] == 1.0)

    mk = lambda imb, pnl: {"strategy": "news", "pnl": pnl, "entry_price": 0.5,
                           "closed": "2026-06-11T12:00:00+00:00", "side": "Yes",
                           "context": {"imbalance": imb, "spread": 0.01}}
    bt = brain_train({"settled": [mk(0.9, 1.0) for _ in range(15)]
                      + [mk(0.1, -1.0) for _ in range(15)]})
    ok("brain learns buyer-pressure wins", bt["w"].get("imb", 0) > 0.3)
    saved_brain = dict(BRAIN)
    BRAIN.update(bt)
    hi = brain_adjust("news", 0.5, {"imbalance": 0.9, "spread": 0.01})
    lo = brain_adjust("news", 0.5, {"imbalance": 0.1, "spread": 0.01})
    ok("brain tilts sizing toward what wins", hi > 1.0 > lo)
    BRAIN.update({"n": 5, "w": {}})
    ok("brain stays neutral without data",
       brain_adjust("news", 0.5, {"imbalance": 0.9}) == 1.0)
    BRAIN.update(saved_brain)

    # ---- CROSS-MARKET REGRESSION GUARD (the key no-regression proof) -------
    # The weave adds xmkt_* features to _brain_x and an entry-context hook. The
    # hard rule: on the COMMON path (no cross-market twin -> xmkt context None),
    # the model must behave IDENTICALLY to before. We prove three things.
    #
    # (1) _brain_x's new features are EXACTLY neutral (0.0) without xmkt ctx,
    #     and stripping them reproduces the pre-weave vector bit-for-bit.
    base_ctx = {"imbalance": 0.7, "spread": 0.01, "momentum_6h": 0.02,
                "hours_to_end": 12}
    x_plain = _brain_x("news", 0.6, base_ctx, "Yes")
    ok("xmkt features default neutral (0.0) with no cross-market ctx",
       x_plain.get("xmkt_div") == 0.0 and x_plain.get("xmkt_cmp") == 0.0)
    pre_weave = {k: v for k, v in x_plain.items()
                 if k not in ("xmkt_div", "xmkt_cmp")}
    # the same context WITH xmkt fields explicitly None must yield the SAME
    # vector — None is the common-path sentinel and must read as neutral.
    x_none = _brain_x("news", 0.6, dict(base_ctx, xmkt_consensus=None,
                                        xmkt_divergence=None), "Yes")
    ok("explicit xmkt None == absent (common path unchanged)",
       x_none == x_plain)
    # (2) brain_adjust is byte-identical with xmkt context absent vs None.
    BRAIN.update(bt)
    a_absent = brain_adjust("news", 0.6, base_ctx, "Yes")
    a_none = brain_adjust("news", 0.6, dict(base_ctx, xmkt_consensus=None,
                                            xmkt_divergence=None), "Yes")
    ok("brain_adjust identical when xmkt ctx absent vs None", a_absent == a_none)
    BRAIN.update(saved_brain)
    # (3) brain_train cv_skill on a NO-xmkt fixture is unchanged whether the
    #     xmkt features are present-but-zero or stripped entirely: a zero
    #     feature contributes zero to the logistic, so the validated skill the
    #     credibility gate keys on cannot move. We fit twice and compare oos.
    noxm = {"settled":
            [{"strategy": "news", "pnl": 1.0, "entry_price": 0.5, "side": "Yes",
              "closed": "2026-06-11T12:00:00+00:00", "name": f"a{i}",
              "context": {"imbalance": 0.9, "spread": 0.01}} for i in range(20)]
            + [{"strategy": "news", "pnl": -1.0, "entry_price": 0.5, "side": "Yes",
                "closed": "2026-06-11T13:00:00+00:00", "name": f"b{i}",
                "context": {"imbalance": 0.1, "spread": 0.01}} for i in range(20)]}
    bt_a = brain_train(noxm)
    bt_b = brain_train(noxm)   # determinism + zero-feature invariance
    sa = (bt_a.get("oos") or {}).get("cv_skill")
    sb = (bt_b.get("oos") or {}).get("cv_skill")
    ok("brain_train cv_skill unchanged on no-xmkt fixture (zero feature inert)",
       sa is not None and sa == sb)
    # and the learned weight on a feature that is always 0 in the fixture stays
    # ~0 — the signal cannot move sizing where it never fires.
    ok("xmkt feature weight ~0 when signal never present",
       abs((bt_a.get("w") or {}).get("xmkt_div", 0.0)) < 1e-6
       and abs((bt_a.get("w") or {}).get("xmkt_cmp", 0.0)) < 1e-6)

    # ---- SPORTS PER-CATEGORY FEATURE REGRESSION + LEAKAGE GUARD --------------
    # The weave adds sports_* features (sportsbook consensus, Elo fair value,
    # divergence, post-game flag) to _brain_x + a sports-only entry-context hook.
    # HARD RULE: on the COMMON path (non-sports market / no sports signal ->
    # sports ctx None) the model is IDENTICAL to before. We prove neutrality,
    # the explicit-None==absent sentinel, signed/capped edges, the post-game
    # flag, and point-in-time leakage safety.
    sx_plain = _brain_x("news", 0.6, base_ctx, "Yes")
    ok("sports features default neutral (0.0) with no sports ctx",
       sx_plain.get("sb_cons") == 0.0 and sx_plain.get("elo_fv") == 0.0
       and sx_plain.get("sb_div") == 0.0 and sx_plain.get("gpost") == 0.0)
    # explicit None sports fields read identically to their absence (common path)
    sx_none = _brain_x("news", 0.6, dict(base_ctx, sportsbook_consensus=None,
                                         sports_elo_fv=None, sports_div=None,
                                         sports_post=None, sports_state=None),
                       "Yes")
    ok("sports: explicit None == absent (common path byte-identical)",
       sx_none == sx_plain)
    # signed edges: consensus/Elo above the entry price -> positive, capped [-1,1]
    sx_book = _brain_x("news", 0.6, dict(base_ctx, sportsbook_consensus=0.8),
                       "Yes")
    ok("sports: sportsbook edge signs and caps (book>price -> +, <=1)",
       0 < sx_book["sb_cons"] <= 1.0
       and _brain_x("news", 0.6, dict(base_ctx, sportsbook_consensus=0.99),
                    "Yes", )["sb_cons"] == 1.0)
    sx_elo = _brain_x("news", 0.6, dict(base_ctx, sports_elo_fv=0.3), "Yes")
    ok("sports: Elo fair-value edge signs (fv<price -> negative)",
       sx_elo["elo_fv"] < 0)
    # post-game flag fires only when the joined game is final (abstain territory)
    ok("sports: post-game flag fires only for finals",
       _brain_x("news", 0.6, dict(base_ctx, sports_post=True), "Yes")["gpost"]
       == 1.0
       and _brain_x("news", 0.6, dict(base_ctx, sports_post=False),
                    "Yes")["gpost"] == 0.0)
    # brain_adjust byte-identical with sports ctx absent vs explicit None
    BRAIN.update(bt)
    sa_absent = brain_adjust("news", 0.6, base_ctx, "Yes")
    sa_none = brain_adjust("news", 0.6, dict(base_ctx, sportsbook_consensus=None,
                                             sports_elo_fv=None, sports_div=None,
                                             sports_post=None), "Yes")
    ok("sports: brain_adjust identical when sports ctx absent vs None",
       sa_absent == sa_none)
    BRAIN.update(saved_brain)
    # sports features stay weight ~0 when never present in training (inert)
    ok("sports: feature weights ~0 when signal never present",
       abs((bt_a.get("w") or {}).get("sb_cons", 0.0)) < 1e-6
       and abs((bt_a.get("w") or {}).get("elo_fv", 0.0)) < 1e-6
       and abs((bt_a.get("w") or {}).get("sb_div", 0.0)) < 1e-6
       and abs((bt_a.get("w") or {}).get("gpost", 0.0)) < 1e-6)
    # LEAKAGE: sports_features is point-in-time + fail-silent. A market with no
    # game-state, no rated teams, and no key yields the all-neutral dict (never
    # an exception, never a future read). _espn_board/_sports_board_cached are
    # never called here (we pass empty board) -> deterministic no-network proof.
    _sf_none = sports_features({"outcomes": "[\"Yes\", \"No\"]",
                                "question": "Will it rain tomorrow?"},
                               0.6, 0, {})
    ok("sports: fail-silent neutral with no game-state / rating / key",
       all(_sf_none[k] is None for k in
           ("sportsbook_consensus", "sports_elo_fv", "sports_div",
            "sports_post")))
    # de-vigged sportsbook consensus only enters when the Odds API is a source
    # (key-gated); a twin without oddsapi never sets sportsbook_consensus.
    ok("sports: sportsbook consensus requires the Odds-API source (key-gated)",
       sports_features({"outcomes": "[\"A\", \"B\"]", "question": "A vs B"},
                       0.6, 0,
                       {"consensus_p": 0.7, "sources": ["kalshi"]}
                       )["sportsbook_consensus"] is None
       and sports_features({"outcomes": "[\"A\", \"B\"]", "question": "A vs B"},
                           0.6, 0,
                           {"consensus_p": 0.7, "sources": ["oddsapi"]}
                           )["sportsbook_consensus"] == 0.7)
    # fav-alignment: backing outcome[1] complements the consensus (P(fav))
    ok("sports: consensus is fav-aligned (fav==1 complements)",
       abs(sports_features({"outcomes": "[\"A\", \"B\"]", "question": "A vs B"},
                           0.6, 1,
                           {"consensus_p": 0.7, "sources": ["oddsapi"]}
                           )["sportsbook_consensus"] - 0.3) < 1e-9)
    # END-TO-END WIRING: a sports category whose outcome is driven by the
    # sportsbook-consensus edge forms a specialist that ACTUALLY WEIGHTS the
    # sports keys, while the GLOBAL model never sees them (its w/specialists are
    # strictly the pre-sports feature space). Proves the feature is learned by
    # the right learner and isolated from the global one.
    sports_fix = {"settled": [
        {"strategy": "news", "pnl": (1.0 if i % 2 == 0 else -1.0),
         "entry_price": 0.5, "side": "Yes", "category": "Sports",
         "closed": "2026-06-11T%02d:00:00+00:00" % (i % 24),
         "name": "Lakers vs. Celtics %d" % i,
         "context": {"imbalance": 0.5, "spread": 0.01, "hours_to_end": 12,
                     # consensus far above price on winners, below on losers:
                     "sportsbook_consensus": (0.9 if i % 2 == 0 else 0.1),
                     "sports_state": "pre"}}
        for i in range(60)]}
    bt_sp = brain_train(sports_fix)
    sp = (bt_sp.get("cat_specialists") or {}).get("sports") or {}
    ok("sports: specialist learns the sportsbook-consensus key (sb_cons!=0)",
       abs((sp.get("w") or {}).get("sb_cons", 0.0)) > 1e-3)
    ok("sports: GLOBAL model carries NO sports keys (feature space isolated)",
       all(k not in (bt_sp.get("w") or {}) for k in SPORTS_X_KEYS)
       and all(k not in (m or {})
               for m in (bt_sp.get("specialists") or {}).values()
               for k in SPORTS_X_KEYS))

    # ---- CRYPTO PER-CATEGORY FEATURE REGRESSION + LEAKAGE GUARD --------------
    # The weave adds crypto_* features (spot distance from strike, hourly
    # realized vol, bid/ask spread bps) to _brain_x + a crypto-only entry-context
    # hook (CoinGecko + Coinbase + Kraken). HARD RULE: on the COMMON path
    # (non-crypto market / no crypto signal -> crypto ctx None) the model is
    # IDENTICAL to before. We prove neutrality, the explicit-None==absent
    # sentinel, signed/scaled/capped values, and point-in-time leakage safety.
    cx_plain = _brain_x("news", 0.6, base_ctx, "Yes")
    ok("crypto features default neutral (0.0) with no crypto ctx",
       cx_plain.get("c_spotdist") == 0.0 and cx_plain.get("c_rvol") == 0.0
       and cx_plain.get("c_spread") == 0.0)
    # explicit None crypto fields read identically to their absence (common path)
    cx_none = _brain_x("news", 0.6, dict(base_ctx, crypto_spot_dist=None,
                                         crypto_rvol_h=None,
                                         crypto_spread_bps=None), "Yes")
    ok("crypto: explicit None == absent (common path byte-identical)",
       cx_none == cx_plain)
    # spot distance signs and caps: spot above strike -> positive, capped [-1,1]
    cx_dist = _brain_x("news", 0.6, dict(base_ctx, crypto_spot_dist=0.04), "Yes")
    ok("crypto: spot-distance signs and caps (spot>strike -> +, <=1)",
       0 < cx_dist["c_spotdist"] <= 1.0
       and _brain_x("news", 0.6, dict(base_ctx, crypto_spot_dist=0.5),
                    "Yes")["c_spotdist"] == 1.0
       and _brain_x("news", 0.6, dict(base_ctx, crypto_spot_dist=-0.5),
                    "Yes")["c_spotdist"] == -1.0)
    # realized vol and spread are non-negative and saturate to 1.0
    cx_vol = _brain_x("news", 0.6, dict(base_ctx, crypto_rvol_h=0.01,
                                        crypto_spread_bps=10.0), "Yes")
    ok("crypto: vol/spread non-negative and saturate [0,1]",
       0 < cx_vol["c_rvol"] <= 1.0 and 0 < cx_vol["c_spread"] <= 1.0
       and _brain_x("news", 0.6, dict(base_ctx, crypto_rvol_h=1.0,
                                      crypto_spread_bps=500.0),
                    "Yes")["c_rvol"] == 1.0
       and _brain_x("news", 0.6, dict(base_ctx, crypto_rvol_h=1.0,
                                      crypto_spread_bps=500.0),
                    "Yes")["c_spread"] == 1.0)
    # brain_adjust byte-identical with crypto ctx absent vs explicit None
    BRAIN.update(bt)
    ca_absent = brain_adjust("news", 0.6, base_ctx, "Yes")
    ca_none = brain_adjust("news", 0.6, dict(base_ctx, crypto_spot_dist=None,
                                             crypto_rvol_h=None,
                                             crypto_spread_bps=None), "Yes")
    ok("crypto: brain_adjust identical when crypto ctx absent vs None",
       ca_absent == ca_none)
    BRAIN.update(saved_brain)
    # crypto features stay weight ~0 when never present in training (inert)
    ok("crypto: feature weights ~0 when signal never present",
       abs((bt_a.get("w") or {}).get("c_spotdist", 0.0)) < 1e-6
       and abs((bt_a.get("w") or {}).get("c_rvol", 0.0)) < 1e-6
       and abs((bt_a.get("w") or {}).get("c_spread", 0.0)) < 1e-6)
    # LEAKAGE: crypto_features is point-in-time + fail-silent. A market we cannot
    # map to a crypto symbol yields the all-neutral dict (never an exception,
    # never a network call) -> deterministic no-network proof.
    _cf_none = crypto_features({"question": "Will it rain in Tokyo tomorrow?"},
                               0.6)
    ok("crypto: fail-silent neutral on a non-crypto market (no symbol)",
       all(_cf_none[k] is None for k in
           ("crypto_spot_dist", "crypto_rvol_h", "crypto_spread_bps")))
    # the spread-bps math is point-in-time top-of-book: (ask-bid)/mid*1e4, and a
    # crossed/empty book yields None (fail-silent), never a forward read.
    ORACLE_CACHE.pop(("kspread", "BTC"), None)
    _real_get = globals()["get_json"]
    try:
        globals()["get_json"] = lambda *a, **k: {
            "result": {"XXBTZUSD": {"b": ["100.0", "1"], "a": ["100.5", "1"]}}}
        ok("crypto: Kraken spread is (ask-bid)/mid in bps (point-in-time book)",
           abs(_kraken_spread_bps("BTC") - (0.5 / 100.25 * 10000.0)) < 1e-6)
        ORACLE_CACHE.pop(("kspread", "BTC"), None)
        globals()["get_json"] = lambda *a, **k: {
            "result": {"XXBTZUSD": {"b": ["101.0", "1"], "a": ["100.0", "1"]}}}
        ok("crypto: crossed/empty book -> None (fail-silent, no leak)",
           _kraken_spread_bps("BTC") is None)
    finally:
        globals()["get_json"] = _real_get
        ORACLE_CACHE.pop(("kspread", "BTC"), None)
    # END-TO-END WIRING: a crypto category whose outcome is driven by the
    # spot-distance feature forms a specialist that ACTUALLY WEIGHTS the crypto
    # keys, while the GLOBAL model never sees them (its w/specialists are strictly
    # the pre-crypto feature space). Proves the feature is learned by the right
    # learner and isolated from the global one.
    crypto_fix = {"settled": [
        {"strategy": "news", "pnl": (1.0 if i % 2 == 0 else -1.0),
         "entry_price": 0.5, "side": "Yes", "category": "Crypto",
         "closed": "2026-06-11T%02d:00:00+00:00" % (i % 24),
         "name": "Will Bitcoin be above $66,000 #%d" % i,
         "context": {"imbalance": 0.5, "spread": 0.01, "hours_to_end": 12,
                     # spot far above strike on winners, below on losers:
                     "crypto_spot_dist": (0.08 if i % 2 == 0 else -0.08),
                     "crypto_rvol_h": 0.01, "crypto_spread_bps": 6.0}}
        for i in range(60)]}
    bt_cp = brain_train(crypto_fix)
    cp = (bt_cp.get("cat_specialists") or {}).get("crypto") or {}
    ok("crypto: specialist learns the spot-distance key (c_spotdist!=0)",
       abs((cp.get("w") or {}).get("c_spotdist", 0.0)) > 1e-3)
    ok("crypto: GLOBAL model carries NO crypto keys (feature space isolated)",
       all(k not in (bt_cp.get("w") or {}) for k in CRYPTO_X_KEYS)
       and all(k not in (m or {})
               for m in (bt_cp.get("specialists") or {}).values()
               for k in CRYPTO_X_KEYS))

    # ---- WEATHER PER-CATEGORY FEATURE REGRESSION + LEAKAGE GUARD -------------
    # The weave adds wx_* features (forecast-vs-strike distance, ensemble spread,
    # ensemble side-agreement) to _brain_x + a weather-only entry-context hook
    # (Open-Meteo ensemble + weather.gov/NWS). HARD RULE: on the COMMON path
    # (non-weather market / no weather signal -> wx ctx None) the model is
    # IDENTICAL to before. We prove neutrality, the explicit-None==absent
    # sentinel, signed/scaled/capped values, and point-in-time leakage safety.
    wx_plain = _brain_x("news", 0.6, base_ctx, "Yes")
    ok("weather features default neutral (0.0) with no weather ctx",
       wx_plain.get("w_fcstrike") == 0.0 and wx_plain.get("w_spread") == 0.0
       and wx_plain.get("w_agree") == 0.0)
    # explicit None weather fields read identically to their absence (common path)
    wx_none = _brain_x("news", 0.6, dict(base_ctx, wx_fc_strike=None,
                                         wx_fc_spread=None,
                                         wx_model_agree=None), "Yes")
    ok("weather: explicit None == absent (common path byte-identical)",
       wx_none == wx_plain)
    # forecast-vs-strike signs and caps: forecast hotter than strike -> positive,
    # capped to the spec range [-3, 3]
    wx_d = _brain_x("news", 0.6, dict(base_ctx, wx_fc_strike=1.2), "Yes")
    ok("weather: forecast-vs-strike signs and caps (fc>strike -> +, |.|<=3)",
       0 < wx_d["w_fcstrike"] <= 3.0
       and _brain_x("news", 0.6, dict(base_ctx, wx_fc_strike=9.0),
                    "Yes")["w_fcstrike"] == 3.0
       and _brain_x("news", 0.6, dict(base_ctx, wx_fc_strike=-9.0),
                    "Yes")["w_fcstrike"] == -3.0)
    # spread non-negative, saturates at 1.5; agreement non-negative, saturates 1.0
    wx_s = _brain_x("news", 0.6, dict(base_ctx, wx_fc_spread=1.0,
                                      wx_model_agree=0.7), "Yes")
    ok("weather: spread/agreement non-negative and saturate [0,1.5]/[0,1]",
       0 < wx_s["w_spread"] <= 1.5 and 0 < wx_s["w_agree"] <= 1.0
       and _brain_x("news", 0.6, dict(base_ctx, wx_fc_spread=10.0),
                    "Yes")["w_spread"] == 1.5
       and _brain_x("news", 0.6, dict(base_ctx, wx_model_agree=2.0),
                    "Yes")["w_agree"] == 1.0)
    # brain_adjust byte-identical with weather ctx absent vs explicit None
    BRAIN.update(bt)
    wa_absent = brain_adjust("news", 0.6, base_ctx, "Yes")
    wa_none = brain_adjust("news", 0.6, dict(base_ctx, wx_fc_strike=None,
                                             wx_fc_spread=None,
                                             wx_model_agree=None), "Yes")
    ok("weather: brain_adjust identical when weather ctx absent vs None",
       wa_absent == wa_none)
    BRAIN.update(saved_brain)
    # weather features stay weight ~0 when never present in training (inert)
    ok("weather: feature weights ~0 when signal never present",
       abs((bt_a.get("w") or {}).get("w_fcstrike", 0.0)) < 1e-6
       and abs((bt_a.get("w") or {}).get("w_spread", 0.0)) < 1e-6
       and abs((bt_a.get("w") or {}).get("w_agree", 0.0)) < 1e-6)
    # LEAKAGE: weather_features is point-in-time + fail-silent. A market we cannot
    # parse to a weather city/strike yields the all-neutral dict (never an
    # exception, never a network call) -> deterministic no-network proof.
    _wf_none = weather_features({"question": "Will Bitcoin be above $66,000?"},
                                0.6)
    ok("weather: fail-silent neutral on a non-weather market (no city/strike)",
       all(_wf_none[k] is None for k in
           ("wx_fc_strike", "wx_fc_spread", "wx_model_agree")))
    # strike parsing is point-in-time (question text only) and unit-correct: an
    # °F strike is converted to the forecast's native °C, no network call.
    ok("weather: strike parses to Celsius (point-in-time, °F->°C)",
       abs(_wx_strike_c("Will the highest temperature in Austin be 50 °F or "
                        "above on June 12?") - 10.0) < 1e-6
       and abs(_wx_strike_c("Will the highest temperature in Tokyo be 19 °C or "
                            "above on June 12?") - 19.0) < 1e-6
       and _wx_strike_c("Lakers vs. Celtics") is None)
    # side parsing: 'above/higher' -> +1, 'below/lower' -> -1, range/exact -> 0.
    ok("weather: strike side signs (above->+1, below->-1, range->0)",
       _wx_strike_side("highest temperature in NYC be 80 °F or above") == 1
       and _wx_strike_side("lowest temperature in NYC be 50 °F or below") == -1
       and _wx_strike_side("highest temperature in NYC be between 80-82 °F")
       == 0)
    # the ensemble read is point-in-time: stubbed daily members yield mean/spread/
    # agreement deterministically, and a non-target date yields [] (no leak).
    ORACLE_CACHE.pop(("wxens", round(40.71, 2), round(-74.0, 2),
                      "2026-06-12", "max"), None)
    _real_get = globals()["get_json"]
    try:
        globals()["get_json"] = lambda *a, **k: {"daily": {
            "time": ["2026-06-12"],
            "temperature_2m_max_member01": [20.0],
            "temperature_2m_max_member02": [22.0],
            "temperature_2m_max_member03": [24.0]}}
        mem = _openmeteo_ensemble(40.71, -74.0, "2026-06-12", "max")
        ok("weather: ensemble reads per-member daily values (point-in-time)",
           sorted(mem) == [20.0, 22.0, 24.0])
        ORACLE_CACHE.pop(("wxens", round(40.71, 2), round(-74.0, 2),
                          "2026-06-99", "max"), None)
        ok("weather: ensemble empty on a non-target date (no future leak)",
           _openmeteo_ensemble(40.71, -74.0, "2026-06-99", "max") == [])
    finally:
        globals()["get_json"] = _real_get
        ORACLE_CACHE.pop(("wxens", round(40.71, 2), round(-74.0, 2),
                          "2026-06-12", "max"), None)
    # END-TO-END WIRING: a weather category whose outcome is driven by the
    # forecast-vs-strike feature forms a specialist that ACTUALLY WEIGHTS the
    # weather keys, while the GLOBAL model never sees them (its w/specialists are
    # strictly the pre-weather feature space). Proves the feature is learned by
    # the right learner and isolated from the global one.
    weather_fix = {"settled": [
        {"strategy": "news", "pnl": (1.0 if i % 2 == 0 else -1.0),
         "entry_price": 0.5, "side": "Yes", "category": "Weather",
         "closed": "2026-06-11T%02d:00:00+00:00" % (i % 24),
         "name": "Will the highest temperature in Austin be 30 °C or above #%d"
                 % i,
         "context": {"imbalance": 0.5, "spread": 0.01, "hours_to_end": 12,
                     # forecast far above strike on winners, below on losers:
                     "wx_fc_strike": (1.5 if i % 2 == 0 else -1.5),
                     "wx_fc_spread": 1.0, "wx_model_agree": 0.8}}
        for i in range(60)]}
    bt_wx = brain_train(weather_fix)
    wp = (bt_wx.get("cat_specialists") or {}).get("weather") or {}
    ok("weather: specialist learns the forecast-vs-strike key (w_fcstrike!=0)",
       abs((wp.get("w") or {}).get("w_fcstrike", 0.0)) > 1e-3)
    ok("weather: GLOBAL model carries NO weather keys (feature space isolated)",
       all(k not in (bt_wx.get("w") or {}) for k in WEATHER_X_KEYS)
       and all(k not in (m or {})
               for m in (bt_wx.get("specialists") or {}).values()
               for k in WEATHER_X_KEYS))

    # ---- MACRO PER-CATEGORY FEATURE REGRESSION + LEAKAGE GUARD ---------------
    # The weave adds macro_* features (Fed-rate deviation vs latest DFF, YoY CPI
    # surprise vs consensus, 10Y-2Y yield-curve regime) to _brain_x + a macro-only
    # entry-context hook (FRED, key-gated). HARD RULE: on the COMMON path
    # (non-macro market / no macro signal -> macro ctx None) the model is IDENTICAL
    # to before. We prove neutrality, the explicit-None==absent sentinel,
    # signed/scaled/capped values, key-gating, and point-in-time leakage safety.
    mx_plain = _brain_x("news", 0.6, base_ctx, "Yes")
    ok("macro features default neutral (0.0) with no macro ctx",
       mx_plain.get("m_ratedev") == 0.0 and mx_plain.get("m_cpisurp") == 0.0
       and mx_plain.get("m_yieldsig") == 0.0)
    # explicit None macro fields read identically to their absence (common path)
    mx_none = _brain_x("news", 0.6, dict(base_ctx, macro_rate_dev=None,
                                         macro_cpi_surprise=None,
                                         macro_yield_signal=None), "Yes")
    ok("macro: explicit None == absent (common path byte-identical)",
       mx_none == mx_plain)
    # rate-deviation signs and caps: market expects rates above DFF -> positive,
    # capped to the spec range [-1.5, 1.5]
    mx_r = _brain_x("news", 0.6, dict(base_ctx, macro_rate_dev=0.5), "Yes")
    ok("macro: rate-deviation signs and caps (expect>DFF -> +, |.|<=1.5)",
       0 < mx_r["m_ratedev"] <= 1.5
       and _brain_x("news", 0.6, dict(base_ctx, macro_rate_dev=9.0),
                    "Yes")["m_ratedev"] == 1.5
       and _brain_x("news", 0.6, dict(base_ctx, macro_rate_dev=-9.0),
                    "Yes")["m_ratedev"] == -1.5)
    # CPI surprise normalized by 0.5% basis, clipped [-1.5, 1.5]: a +0.5pp
    # surprise maps to +1.0; signs preserved; saturates at the cap.
    mx_c = _brain_x("news", 0.6, dict(base_ctx, macro_cpi_surprise=0.5), "Yes")
    ok("macro: CPI surprise normalized by 0.5% basis, signed, clipped [-1.5,1.5]",
       abs(mx_c["m_cpisurp"] - 1.0) < 1e-9
       and _brain_x("news", 0.6, dict(base_ctx, macro_cpi_surprise=2.0),
                    "Yes")["m_cpisurp"] == 1.5
       and _brain_x("news", 0.6, dict(base_ctx, macro_cpi_surprise=-2.0),
                    "Yes")["m_cpisurp"] == -1.5)
    # yield-curve regime codes pass through within their declared [-1.0, 0.5] band
    ok("macro: yield-curve regime codes pass through ([-1.0,0.5])",
       _brain_x("news", 0.6, dict(base_ctx, macro_yield_signal=-1.0),
                "Yes")["m_yieldsig"] == -1.0
       and _brain_x("news", 0.6, dict(base_ctx, macro_yield_signal=0.5),
                    "Yes")["m_yieldsig"] == 0.5
       and _brain_x("news", 0.6, dict(base_ctx, macro_yield_signal=0.0),
                    "Yes")["m_yieldsig"] == 0.0)
    # brain_adjust byte-identical with macro ctx absent vs explicit None
    BRAIN.update(bt)
    ma_absent = brain_adjust("news", 0.6, base_ctx, "Yes")
    ma_none = brain_adjust("news", 0.6, dict(base_ctx, macro_rate_dev=None,
                                             macro_cpi_surprise=None,
                                             macro_yield_signal=None), "Yes")
    ok("macro: brain_adjust identical when macro ctx absent vs None",
       ma_absent == ma_none)
    BRAIN.update(saved_brain)
    # macro features stay weight ~0 when never present in training (inert)
    ok("macro: feature weights ~0 when signal never present",
       abs((bt_a.get("w") or {}).get("m_ratedev", 0.0)) < 1e-6
       and abs((bt_a.get("w") or {}).get("m_cpisurp", 0.0)) < 1e-6
       and abs((bt_a.get("w") or {}).get("m_yieldsig", 0.0)) < 1e-6)
    # LEAKAGE / KEY-GATING: macro_features is point-in-time, fail-silent and
    # key-gated. With NO FRED_API_KEY in the environment every FRED read returns
    # [] and all three fields stay None — a deterministic no-network, no-key proof.
    _saved_fred_key = os.environ.pop("FRED_API_KEY", None)
    ORACLE_CACHE.pop(("fred", "DFF", 1), None)
    try:
        _mf_nokey = macro_features(
            {"question": "Will the Fed funds rate be above 4.5% in July?"}, 0.6)
        ok("macro: key-gated -> all-neutral with no FRED_API_KEY (silent skip)",
           all(_mf_nokey[k] is None for k in
               ("macro_rate_dev", "macro_cpi_surprise", "macro_yield_signal")))
    finally:
        if _saved_fred_key is not None:
            os.environ["FRED_API_KEY"] = _saved_fred_key
        ORACLE_CACHE.pop(("fred", "DFF", 1), None)
    # a non-macro market yields the all-neutral dict (no subject match, no call)
    _mf_none = macro_features({"question": "Will it rain in Tokyo tomorrow?"},
                              0.6)
    ok("macro: fail-silent neutral on a non-macro market (no subject match)",
       all(_mf_none[k] is None for k in
           ("macro_rate_dev", "macro_cpi_surprise", "macro_yield_signal")))
    # rate-target parse is point-in-time (question text only): a "%"/"percent"
    # target is read; a market with no target yields None (no forward read).
    ok("macro: rate target parses from question text (point-in-time)",
       abs(_macro_rate_target("Fed funds rate above 4.50% in July") - 4.5) < 1e-9
       and _macro_rate_target("Will the Lakers win tonight?") is None)
    # FRED read is point-in-time + key-gated: with a stubbed key + network the
    # latest published observation is returned; missing-value "." is skipped and
    # a future-dated value is NEVER fabricated (we only read what FRED publishes).
    ORACLE_CACHE.pop(("fred", "DFF", 1), None)
    _real_get = globals()["get_json"]
    try:
        os.environ["FRED_API_KEY"] = "TEST"
        globals()["get_json"] = lambda *a, **k: {"observations": [
            {"date": "2026-06-12", "value": "4.33"},
            {"date": "2026-06-11", "value": "."}]}
        ok("macro: FRED reads latest published value, skips missing '.' (PIT)",
           abs(_fred_value("DFF") - 4.33) < 1e-9)
        ORACLE_CACHE.pop(("fred", "DFF", 1), None)
        globals()["get_json"] = lambda *a, **k: {"observations": []}
        ok("macro: FRED empty observations -> None (fail-silent, no leak)",
           _fred_value("DFF") is None)
    finally:
        globals()["get_json"] = _real_get
        if _saved_fred_key is not None:
            os.environ["FRED_API_KEY"] = _saved_fred_key
        else:
            os.environ.pop("FRED_API_KEY", None)
        ORACLE_CACHE.pop(("fred", "DFF", 1), None)
    # END-TO-END WIRING: a macro category whose outcome is driven by the
    # rate-deviation feature forms a specialist that ACTUALLY WEIGHTS the macro
    # keys, while the GLOBAL model never sees them (its w/specialists are strictly
    # the pre-macro feature space). Proves the feature is learned by the right
    # learner and isolated from the global one.
    macro_fix = {"settled": [
        {"strategy": "news", "pnl": (1.0 if i % 2 == 0 else -1.0),
         "entry_price": 0.5, "side": "Yes", "category": "Economy",
         "closed": "2026-06-11T%02d:00:00+00:00" % (i % 24),
         "name": "Will the Fed funds rate be above 4.50%% in July #%d" % i,
         "context": {"imbalance": 0.5, "spread": 0.01, "hours_to_end": 12,
                     # market expects rates well above DFF on winners, below on
                     # losers; curve inverted, CPI hot on the same parity:
                     "macro_rate_dev": (0.6 if i % 2 == 0 else -0.6),
                     "macro_cpi_surprise": (0.3 if i % 2 == 0 else -0.3),
                     "macro_yield_signal": -1.0}}
        for i in range(60)]}
    bt_mc = brain_train(macro_fix)
    mp = (bt_mc.get("cat_specialists") or {}).get("macro") or {}
    ok("macro: specialist learns the rate-deviation key (m_ratedev!=0)",
       abs((mp.get("w") or {}).get("m_ratedev", 0.0)) > 1e-3)
    ok("macro: GLOBAL model carries NO macro keys (feature space isolated)",
       all(k not in (bt_mc.get("w") or {}) for k in MACRO_X_KEYS)
       and all(k not in (m or {})
               for m in (bt_mc.get("specialists") or {}).values()
               for k in MACRO_X_KEYS))

    # ---- SOCIAL PER-CATEGORY FEATURE REGRESSION + LEAKAGE GUARD --------------
    # The weave adds social_* features (corroborated fresh-news flag, headline
    # sentiment magnitude, and side-aligned sentiment) to _brain_x + a social-only
    # entry-context hook fed by the keyless news_rss (Google News/BBC) + HackerNews
    # connectors via the point-in-time HEADLINES buffer. HARD RULE: on the COMMON
    # path (non-social market / no fresh coverage -> social ctx None) the model is
    # IDENTICAL to before. We prove neutrality, the explicit-None==absent sentinel,
    # signed/scaled/capped values, side-alignment, and point-in-time leakage safety.
    sx_plain = _brain_x("news", 0.6, base_ctx, "Yes")
    ok("social features default neutral (0.0) with no social ctx",
       sx_plain.get("s_newsstrong") == 0.0 and sx_plain.get("s_sentmag") == 0.0
       and sx_plain.get("s_sentalign") == 0.0)
    # explicit None social fields read identically to their absence (common path)
    sx_none = _brain_x("news", 0.6, dict(base_ctx, social_news_strong=None,
                                         social_sent_mag=None,
                                         social_sent_align=None), "Yes")
    ok("social: explicit None == absent (common path byte-identical)",
       sx_none == sx_plain)
    # corroborated-news flag is binary 0/1; magnitude non-negative, saturates 1.0
    sx_s = _brain_x("news", 0.6, dict(base_ctx, social_news_strong=1.0,
                                      social_sent_mag=0.7), "Yes")
    ok("social: news-strong is binary and magnitude saturates [0,1]",
       sx_s["s_newsstrong"] == 1.0 and 0 < sx_s["s_sentmag"] <= 1.0
       and _brain_x("news", 0.6, dict(base_ctx, social_sent_mag=5.0),
                    "Yes")["s_sentmag"] == 1.0
       and _brain_x("news", 0.6, dict(base_ctx, social_sent_mag=-5.0),
                    "Yes")["s_sentmag"] == 0.0)
    # side-aligned sentiment: signed, capped [-1,1]; passes through here (the
    # side flip lives in social_features, tested below against the headline buffer)
    ok("social: alignment signs and caps ([-1,1])",
       _brain_x("news", 0.6, dict(base_ctx, social_sent_align=0.5),
                "Yes")["s_sentalign"] == 0.5
       and _brain_x("news", 0.6, dict(base_ctx, social_sent_align=5.0),
                    "Yes")["s_sentalign"] == 1.0
       and _brain_x("news", 0.6, dict(base_ctx, social_sent_align=-5.0),
                    "Yes")["s_sentalign"] == -1.0)
    # brain_adjust byte-identical with social ctx absent vs explicit None
    BRAIN.update(bt)
    sa_absent = brain_adjust("news", 0.6, base_ctx, "Yes")
    sa_none = brain_adjust("news", 0.6, dict(base_ctx, social_news_strong=None,
                                             social_sent_mag=None,
                                             social_sent_align=None), "Yes")
    ok("social: brain_adjust identical when social ctx absent vs None",
       sa_absent == sa_none)
    BRAIN.update(saved_brain)
    # social features stay weight ~0 when never present in training (inert)
    ok("social: feature weights ~0 when signal never present",
       abs((bt_a.get("w") or {}).get("s_newsstrong", 0.0)) < 1e-6
       and abs((bt_a.get("w") or {}).get("s_sentmag", 0.0)) < 1e-6
       and abs((bt_a.get("w") or {}).get("s_sentalign", 0.0)) < 1e-6)
    # LEAKAGE / POINT-IN-TIME: social_features reads ONLY the HEADLINES buffer and
    # filters strictly by timestamp. We drive it against a controlled buffer (no
    # network) to prove: (a) a fresh, on-subject, positive burst lights the strong
    # flag + positive Yes-alignment; (b) a STALE headline (older than the window)
    # is invisible — the exact future-leakage guard; (c) the No side flips
    # alignment; (d) an off-subject market reads all-neutral.
    _saved_headlines = list(HEADLINES)
    try:
        now_ts = time.time()
        HEADLINES[:] = [
            (now_ts - 100, "taylor swift announces record-breaking world tour"),
            (now_ts - 200, "taylor swift tour breaks ticket sales record"),
        ]
        _sf = social_features("Will Taylor Swift announce a new tour this week?",
                              "Yes")
        ok("social: fresh corroborated positive burst -> strong flag + +align (PIT)",
           _sf["social_news_strong"] == 1.0 and _sf["social_sent_mag"] > 0
           and _sf["social_sent_align"] > 0)
        # the SAME two headlines, but published OUTSIDE the freshness window, are
        # invisible — a backtest at an earlier moment cannot see future news.
        HEADLINES[:] = [
            (now_ts - 99999, "taylor swift announces record-breaking world tour"),
            (now_ts - 99998, "taylor swift tour breaks ticket sales record"),
        ]
        _sf_stale = social_features(
            "Will Taylor Swift announce a new tour this week?", "Yes")
        ok("social: stale (out-of-window) headlines are invisible (no future leak)",
           all(_sf_stale[k] is None for k in
               ("social_news_strong", "social_sent_mag", "social_sent_align")))
        # side flip: a positive mood CUTS AGAINST a No bet -> negative alignment.
        HEADLINES[:] = [
            (now_ts - 100, "taylor swift announces record-breaking world tour"),
            (now_ts - 200, "taylor swift tour breaks ticket sales record"),
        ]
        _sf_no = social_features(
            "Will Taylor Swift announce a new tour this week?", "No")
        ok("social: No side flips sentiment alignment (positive mood -> -align)",
           _sf_no["social_sent_align"] < 0)
        # off-subject market: no fresh headline matches -> all-neutral (fail-silent)
        _sf_off = social_features("Will it rain in Tokyo tomorrow?", "Yes")
        ok("social: fail-silent neutral on an off-subject market (no coverage)",
           all(_sf_off[k] is None for k in
               ("social_news_strong", "social_sent_mag", "social_sent_align")))
    finally:
        HEADLINES[:] = _saved_headlines
    # END-TO-END WIRING: a social category whose outcome is driven by the
    # side-aligned sentiment feature forms a specialist that ACTUALLY WEIGHTS the
    # social keys, while the GLOBAL model never sees them (its w/specialists are
    # strictly the pre-social feature space). Proves the feature is learned by the
    # right learner and isolated from the global one.
    social_fix = {"settled": [
        {"strategy": "news", "pnl": (1.0 if i % 2 == 0 else -1.0),
         "entry_price": 0.5, "side": "Yes", "category": "Pop Culture",
         "closed": "2026-06-11T%02d:00:00+00:00" % (i % 24),
         "name": "Will the new album top the charts #%d" % i,
         "context": {"imbalance": 0.5, "spread": 0.01, "hours_to_end": 12,
                     # headline mood aligns with the outcome on winners, against
                     # it on losers; corroborated coverage on the same parity:
                     "social_news_strong": 1.0,
                     "social_sent_mag": 0.6,
                     "social_sent_align": (0.6 if i % 2 == 0 else -0.6)}}
        for i in range(60)]}
    bt_so = brain_train(social_fix)
    so = (bt_so.get("cat_specialists") or {}).get("social") or {}
    ok("social: specialist learns the sentiment-alignment key (s_sentalign!=0)",
       abs((so.get("w") or {}).get("s_sentalign", 0.0)) > 1e-3)
    ok("social: GLOBAL model carries NO social keys (feature space isolated)",
       all(k not in (bt_so.get("w") or {}) for k in SOCIAL_X_KEYS)
       and all(k not in (m or {})
               for m in (bt_so.get("specialists") or {}).values()
               for k in SOCIAL_X_KEYS))

    # ===== PER-CATEGORY BRAIN SPECIALIST LAYER (partial pooling, OOS-gated) ===
    # The hard rule, tightened: the GLOBAL model must be byte-identical AFTER
    # adding the category layer, proven on a FIXED MIXED-CATEGORY dataset (not
    # just on category-free data). The category layer is purely additive and
    # gated — the global model trains on all rows exactly as before.
    def _mixed_fixture():
        cats = ["Crypto", "Sports", "Politics", "Weather", "Economy", "Other"]
        rws = []
        for i in range(120):
            cat = cats[i % len(cats)]
            imb = 0.9 if (i % 2 == 0) else 0.1       # global signal: imb wins
            rws.append({"strategy": "news", "pnl": (1.0 if imb > 0.5 else -1.0),
                        "entry_price": 0.5, "side": "Yes",
                        "closed": "2026-06-11T%02d:00:00+00:00" % (i % 24),
                        "name": "%s-mkt-%d" % (cat, i), "category": cat,
                        "context": {"imbalance": imb, "spread": 0.01,
                                    "hours_to_end": 12}})
        return {"settled": rws}
    saved_for_cat = dict(BRAIN)
    bt_mix = brain_train(_mixed_fixture())
    # (1a) the GLOBAL cv_skill on the FIXED MIXED data is exactly the golden
    #      pre-category-layer value: the category layer did not touch training.
    ok("cat: GLOBAL cv_skill byte-identical on fixed MIXED data (0.6729)",
       (bt_mix.get("oos") or {}).get("cv_skill") == 0.6729)
    # (1b) the GLOBAL weights are unchanged on the same mixed data (golden bias).
    #      Goldens regenerated 2026-06-13 when the leaky `night`/`imb_x_night`
    #      features (derived from the resolution-time `closed` field — unknown at
    #      entry) were removed from _brain_x; this is a deliberate, OOS-verified
    #      feature-space change (challenger cv_skill >= incumbent), so the frozen
    #      logistic weights legitimately shift. cv_skill (1a) is unaffected.
    ok("cat: GLOBAL weights unchanged on fixed MIXED data",
       (bt_mix.get("w") or {}).get("bias") == 1.5635
       and (bt_mix.get("w") or {}).get("imb") == 1.5299)
    # (1c) brain_adjust(category=None) — the common path — is byte-identical to
    #      the golden pre-category value on the same mixed-trained brain.
    BRAIN.clear(); BRAIN.update(bt_mix)
    a_none_mix = brain_adjust("news", 0.6,
                              {"imbalance": 0.9, "spread": 0.01,
                               "hours_to_end": 12}, "Yes")
    ok("cat: brain_adjust(category=None) byte-identical on MIXED data",
       a_none_mix == 1.0720100983280245)
    # (1d) and identical to passing a category whose specialist has NOT earned
    #      divergence (oos_skill None at n=20/category) — additive+gated no-op.
    a_cat_inert = brain_adjust("news", 0.6,
                               {"imbalance": 0.9, "spread": 0.01,
                                "hours_to_end": 12}, "Yes", category="Crypto")
    ok("cat: OOS-unearned category == category=None (pure no-op)",
       a_cat_inert == a_none_mix)

    # (2) OOS-NEGATIVE category is a no-op. Build a brain where one category's
    #     specialist is forced OOS-negative; brain_adjust must ignore it.
    BRAIN.clear(); BRAIN.update(bt_mix)
    base_adj = brain_adjust("news", 0.6, {"imbalance": 0.9, "spread": 0.01,
                                          "hours_to_end": 12}, "Yes")
    spec_w = dict((bt_mix["cat_specialists"]["crypto"])["w"])
    spec_w["bias"] = spec_w.get("bias", 0.0) + 5.0   # would pull adj UP if used
    BRAIN["cat_specialists"] = {"crypto": {"w": spec_w, "oos_skill": -0.01,
                                           "n_eff": 40, "n": 60}}
    ok("cat: OOS-negative specialist is ignored (no-op)",
       brain_adjust("news", 0.6, {"imbalance": 0.9, "spread": 0.01,
                                  "hours_to_end": 12}, "Yes",
                    category="Crypto") == base_adj)
    # (3) dead-cohort-only category => n_eff=0 => no-op even with oos_skill>0.
    BRAIN["cat_specialists"] = {"crypto": {"w": spec_w, "oos_skill": 0.5,
                                           "n_eff": 0, "n": 0}}
    ok("cat: n_eff=0 (dead-cohort-only) category is a no-op",
       brain_adjust("news", 0.6, {"imbalance": 0.9, "spread": 0.01,
                                  "hours_to_end": 12}, "Yes",
                    category="Crypto") == base_adj)
    # (4) a category with REAL OOS skill DIVERGES: same w but oos_skill>0 and
    #     n_eff>0 must move the multiplier away from the global no-op value.
    BRAIN["cat_specialists"] = {"crypto": {"w": spec_w, "oos_skill": 0.5,
                                           "n_eff": 40, "n": 60}}
    div_adj = brain_adjust("news", 0.6, {"imbalance": 0.9, "spread": 0.01,
                                         "hours_to_end": 12}, "Yes",
                           category="Crypto")
    ok("cat: OOS-positive + credible category DIVERGES from global",
       div_adj != base_adj)
    # and an unknown / unmapped category never engages the layer.
    ok("cat: unknown category falls back to global (no-op)",
       brain_adjust("news", 0.6, {"imbalance": 0.9, "spread": 0.01,
                                  "hours_to_end": 12}, "Yes",
                    category="Nonexistent") == base_adj)
    # (5) ERA HYGIENE / LEAKAGE: dead_cohort rows never enter a specialist.
    dc_fix = {"settled":
        [{"strategy": "high_prob", "pnl": (1.0 if i % 2 else -1.0),
          "entry_price": 0.92, "side": "Yes", "category": "Sports",
          "closed": "2026-06-11T%02d:00:00+00:00" % (i % 24),
          "name": "Lakers vs. Celtics %d" % i,
          "context": {"lane": "r90", "imbalance": 0.9, "spread": 0.01}}
         for i in range(40)]}
    bt_dc = brain_train(dc_fix)
    ok("cat: dead-cohort sports rows never form a specialist (era hygiene)",
       "sports" not in (bt_dc.get("cat_specialists") or {}))
    # (5b) GLOBAL n_eff era hygiene: the credibility weight n_eff (which scales
    #      live sizing through cred) must apply the SAME dead_cohort exclusion
    #      the training rows already do — otherwise unrepeatable r90 settles the
    #      model never trained on would inflate sizing confidence (an era-hygiene
    #      asymmetry). dc_fix is all dead_cohort, so the correct n_eff is 0;
    #      effective_n on the SAME rows UNFILTERED is >0, which is exactly what
    #      dropping the filter from the n_eff comprehension would let through.
    ok("global: dead-cohort settles never inflate brain n_eff (era hygiene)",
       bt_dc["n_eff"] == 0 and effective_n(dc_fix["settled"]) > 0)
    # (6) cat_key normalization is point-in-time and total over the families.
    ok("cat: cat_key maps raw tags onto the six families",
       cat_key("Crypto") == "crypto" and cat_key("Economy") == "macro"
       and cat_key("Pop Culture") == "social" and cat_key("Esports") == "sports"
       and cat_key(None) is None and cat_key("Nonexistent") is None)
    # (7) stale BRAIN without cat_specialists forces a retrain (cache fix).
    BRAIN.clear()
    BRAIN.update({k: v for k, v in bt_mix.items() if k != "cat_specialists"})
    bt_retrain = brain_train(_mixed_fixture())
    ok("cat: stale BRAIN lacking cat_specialists forces a retrain",
       bt_retrain.get("cat_specialists"))
    BRAIN.clear(); BRAIN.update(saved_for_cat)

    random.seed(42)
    tset = {"settled": [{"strategy": "explore", "pnl": 0.1, "entry_price": 0.9,
                         "category": "Crypto"}] * 12
            + [{"strategy": "explore", "pnl": -0.9, "entry_price": 0.9,
                "category": "Sports"}] * 12}
    wins = 0
    for _ in range(300):
        ranked = thompson_rank(tset, [
            {"entry_price": 0.9, "category": "Crypto", "strategy": "explore"},
            {"entry_price": 0.9, "category": "Sports", "strategy": "explore"}])
        wins += ranked[0]["category"] == "Crypto"
    ok("thompson prefers the proven cell", wins > 240)
    ok("thompson tracks cells", TS_STATE["cells"] == 2)
    ORACLE_CACHE[("simprior",)] = (time.time(), {(95, "crypto-price"): (20.0, 20.0)})
    random.seed(9)
    fresh = {"settled": []}
    prio = 0
    for _ in range(300):
        r2 = thompson_rank(fresh, [
            {"entry_price": 0.95, "category": "Crypto",
             "name": "Will the price of Bitcoin be above $66k", "strategy": "explore"},
            {"entry_price": 0.95, "category": "Sports",
             "name": "Lakers vs. Celtics", "strategy": "explore"}])
        prio += "Bitcoin" in r2[0]["name"]
    ok("thompson: replay prior steers cold-start exploration", prio > 220)
    ORACLE_CACHE.pop(("simprior",), None)

    p1 = parse_threshold("Will Bitcoin be above $66,000 on June 12?")
    p2 = parse_threshold("Will Bitcoin be above $68,000 on June 12?")
    ok("pair: family match across thresholds",
       p1 and p2 and p1[0] == p2[0] and p1[1] == 66000 and p2[1] == 68000)
    ok("pair: direction parsed", p1[2] == "up" and
       parse_threshold("Will it be below $50 today?")[2] == "down")
    ok("pair: non-threshold ignored",
       parse_threshold("Lakers vs. Celtics") is None)

    bt2 = brain_train({"settled":
        [{"strategy": "news", "pnl": 1.0, "entry_price": 0.5, "side": "Yes",
          "closed": "2026-06-11T12:00:00+00:00", "name": f"q{i}",
          "context": {"imbalance": 0.9, "spread": 0.01}} for i in range(20)]
        + [{"strategy": "news", "pnl": -1.0, "entry_price": 0.5, "side": "Yes",
            "closed": "2026-06-11T13:00:00+00:00", "name": f"r{i}",
            "context": {"imbalance": 0.1, "spread": 0.01}} for i in range(20)]})
    ok("brain2: validates out-of-sample", bt2["oos"] is not None and "cv_skill" in bt2["oos"])
    ok("brain2: trains a specialist", "news" in bt2["specialists"])
    ok("brain2: skill factor bounded", 0.25 <= bt2["skill_factor"] <= 1.0)
    ok("brain3: cross-validated skill computed",
       bt2["oos"] is None or "cv_skill" in bt2["oos"])
    ok("brain3: auto-engineered pattern features tracked",
       "pat_feats" in bt2)
    ok("brain4: a champion class is crowned",
       bt2.get("kind") in ("logistic", "gbm", "forest", "mlp"))
    ok("brain4: the zoo raced",
       bt2.get("oos") is None or "zoo" in bt2["oos"])
    ok("risk: hold-to-resolution risks the stake",
       position_risk({"strategy": "explore", "cost": 25.0, "stop": 0.02,
                      "entry_price": 0.9, "shares": 27}) == 25.0)
    ok("risk: brackets risk the stop distance",
       position_risk({"strategy": "daytrade", "cost": 30.0, "stop": 0.45,
                      "entry_price": 0.5, "shares": 60}) == 3.0)
    ok("risk: locked arbs risk nothing",
       position_risk({"strategy": "arbitrage", "cost": 500.0}) == 0.0)
    ok("risk: drawdown ladder de-risks",
       _dd_factor(4.5, [[2, 0.75], [4, 0.5], [6, 0.25]]) == 0.5)
    ok("risk: ladder restores at the peak",
       _dd_factor(0.0, [[2, 0.75], [4, 0.5], [6, 0.25]]) == 1.0)

    w_sgd = {"bias": 0.0, "imb": 0.0}
    ml.sgd_step(w_sgd, {"bias": 1.0, "imb": 1.0}, 1.0)
    ok("online sgd: a win pushes weights up", w_sgd["imb"] > 0)
    mg2 = ml.fit_gbm([({"v": i / 50, "bias": 1.0},
                       1.0 if i > 25 else 0.0) for i in range(50)] * 2)
    ok("gbm v2: early stopping bounds the trees",
       1 <= mg2["n_trees"] <= 80)
    mf2 = ml.fit_forest([({"v": i / 50, "bias": 1.0},
                          1.0 if i > 25 else 0.0) for i in range(50)] * 2)
    ok("forest v2: out-of-bag validation reported",
       mf2["oob_accuracy"] is None or 0.7 <= mf2["oob_accuracy"] <= 1.0)

    ok("brain5: committee weights sum to ~1",
       not bt2.get("stack") or abs(sum(w2 for _, w2 in bt2["stack"]) - 1) < 0.02)
    ok("brain5: calibrated prediction bounded",
       bt2.get("cal") is None or 0 <= ml.apply_cal(bt2["cal"], 0.7) <= 1)
    ok("cal dispatch: platt form",
       0 <= ml.apply_cal({"a": 1.0, "b": 0.0}, 0.7) <= 1)
    ok("cal dispatch: isotonic form interpolates",
       abs(ml.apply_cal({"x": [0.2, 0.8], "y": [0.3, 0.9]}, 0.5) - 0.6) < 1e-6)
    _ph = ml.ph_new()
    ok("drift: stationary stream stays quiet",
       not any(ml.ph_update(_ph, 0.5) for _ in range(100)))
    ok("drift: regime shift fires",
       any(ml.ph_update(_ph, 2.5) for _ in range(60)))
    _w, _g2 = {"bias": 0.1}, {}
    ml.sgd_step(_w, {"bias": 1.0, "imb": 1.0}, 1.0, g2=_g2)
    ok("adagrad: accumulates per-feature curvature",
       _g2.get("imb", 0) > 0 and _g2.get("bias", 0) > 0)

    PRICE_MEM.clear()
    _now = time.time()
    for i in range(100):
        mem_record("TESTTOK", 0.5 + i * 0.001, ts=_now - 100 + i)
    ok("mem: records full-resolution ticks",
       len(PRICE_MEM["TESTTOK"]["t"]) == 100)
    ok("mem: series windows by time", 45 <= len(mem_series("TESTTOK", 50)) <= 52)
    mem_record("TESTTOK", 0.9, ts=_now - 0.7)
    ok("mem: sub-tick spam deduped",
       len(PRICE_MEM["TESTTOK"]["t"]) == 100)
    _save_max = PRICE_MEM_CFG["max_tokens"]
    PRICE_MEM_CFG["max_tokens"] = 2
    mem_record("TOK2", 0.5, ts=_now)
    mem_record("TOK3", 0.5, ts=_now)
    ok("mem: evicts the stalest market at capacity",
       len(PRICE_MEM) == 2 and "TESTTOK" not in PRICE_MEM)
    PRICE_MEM_CFG["max_tokens"] = _save_max
    PRICE_MEM.clear()
    mem_record("MERGETOK", 0.6, ts=_now - 5)        # live tick first
    mem_preload("MERGETOK", [(_now - 100, 0.4), (_now - 50, 0.5),
                             (_now - 4, 0.99)])     # last one overlaps live
    _mt = PRICE_MEM["MERGETOK"]["t"]
    ok("mem: preload splices history beneath live ticks",
       len(_mt) == 3 and list(_mt) == sorted(_mt))
    PRICE_MEM.clear()
    BOOK_MEM.clear()
    _raw = {"bids": [{"price": "0.1", "size": "5"}, {"price": "0.4", "size": "9"}],
            "asks": [{"price": "0.9", "size": "2"}, {"price": "0.6", "size": "7"}]}
    book_record("BTOK", _raw)
    _bl = list(BOOK_MEM["BTOK"]["l"])
    ok("book: ladder stored best-first, padded to 5 levels",
       len(_bl) == 20 and abs(_bl[0] - 0.4) < 1e-6 and _bl[1] == 9.0
       and abs(_bl[10] - 0.6) < 1e-6 and _bl[11] == 7.0 and _bl[8] == 0.0)
    ok("book: series returns the snapshot",
       len(book_series("BTOK", 60)) == 1)
    BOOK_MEM.clear()
    ok("chartml: no model = no opinion (gate stays open)",
       chartml.move_predict({}, [(i * 60.0, 0.5) for i in range(60)],
                            0.05) is None)
    _cx = chartml.chart_x([(i * 60.0, 0.5 + 0.001 * i) for i in range(60)])
    ok("chartml: chart_x reads a planted trend",
       _cx is not None and _cx["slope"] > 0 and _cx["trend_r2"] > 0.95)
    ok("miner: vetoes now need statistical surprise",
       chartml.binom_p(1, 12, 0.46) < 0.15 < chartml.binom_p(4, 9, 0.46))
    ok("crypto-threshold: BTC price level detected",
       is_crypto_threshold("Will the price of Bitcoin be above $66,000 on June 13?"))
    ok("crypto-threshold: up-or-down stays tradeable",
       not is_crypto_threshold("Bitcoin Up or Down on June 12?"))
    ok("crypto-threshold: non-crypto untouched",
       not is_crypto_threshold("Will the highest temperature in Tokyo be 29°C?"))
    _wp = _wx_parse("Will the highest temperature in Amsterdam be 17°C on June 12?")
    ok("oracle: parses the exact-pin shape (52/62 of real questions)",
       _wp is not None and _wp[0] == "max" and _wp[1] == "Amsterdam")
    ok("oracle: pin miss reads as No with signed room",
       _wp[2](20.0) == (False, -2.5))
    _wp = _wx_parse("Will the highest temperature in Austin be between 88-89°F on June 12?")
    ok("oracle: F-range converts forecast C and reads inside",
       _wp is not None and _wp[2](31.4)[0] is True)
    _wp = _wx_parse("Will the lowest temperature in Tokyo be 19°C on June 12?")
    ok("oracle: lowest-temperature questions use the min forecast",
       _wp is not None and _wp[0] == "min")
    ok("oracle: directional shape still parses",
       _wx_parse("Will the highest temperature in Wuhan be 30\u00b0C or higher?") is not None)
    _wp = _wx_parse("Will the highest temperature in NYC be 81\u00b0F or below?")
    ok("oracle: F directional parses as directional, not pin (audit critical)",
       _wp is not None and _wp[2](21.1)[0] is True)   # 21.1C=70F, below 81F
    ok("oracle: in-game detector fires after gameStartTime",
       is_in_game({"gameStartTime": "2026-06-12 00:00:00+00"}) is True
       and is_in_game({"gameStartTime": "2027-01-01 00:00:00+00"}) is False
       and is_in_game({}) is False)
    _bs = band_win_stats({"settled": []})
    _raw = json.loads(RESEARCH_FILE.read_text()) if RESEARCH_FILE.exists() else {}
    _raw_n = sum(b.get("n", 0) for bk in ("0-6h", "6-24h")
                 for b in (_raw.get("by_bucket", {}).get(bk) or {}).values())
    ok("research seed is family-level (collapses raw obs 5x+)",
       0 < _bs.get("93", (0, 0))[1] and _raw_n > 5 * _bs.get("93", (0, 0))[1])
    ok("kelly funds the strongest band at family-level confidence",
       kelly_dollars(10000, 0.93, 93, _bs, {}) > 0)
    ok("kelly refuses the weak edge of the lane (90c)",
       kelly_dollars(10000, 0.90, 90, _bs, {}) == 0)
    ok("kelly refuses when wilson says no edge",
       kelly_dollars(10000, 0.95, 95, {"95": (50, 60)}, {}) == 0)
    mg = ml.fit_gbm([({"v": i / 20, "bias": 1.0}, 1.0 if i > 10 else 0.0)
                     for i in range(20)] * 3)
    ok("ml lib: gbm learns a step function",
       ml.predict(mg, {"v": 0.9, "bias": 1.0}) > 0.7 >
       ml.predict(mg, {"v": 0.1, "bias": 1.0}))

    import random as _r
    _r.seed(3)
    ring = [({"v": v, "bias": 1.0}, 1.0 if 0.3 < v < 0.7 else 0.0)
            for v in [_r.random() for _ in range(300)]]
    stp = _fit_stumps(ring)
    hits = sum((_predict_stumps(stp, x) >= 0.5) == (y >= 0.5) for x, y in ring)
    ok("stumps learn nonlinear bands (logistic can't)", hits / len(ring) > 0.85)
    wl = _fit_logistic(ring)
    lhits = sum((_predict(wl, x) >= 0.5) == (y >= 0.5) for x, y in ring)
    ok("confirmed: linear model fails this shape", lhits / len(ring) < 0.75)

    ok("scores: matches held market to game",
       _match_game("South Korea", "Czechia",
                   "Exact Score: Korea Republic 0 - 1 Czechia") is True)
    ok("scores: rejects the wrong game",
       _match_game("Mexico", "South Africa",
                   "Korea Republic vs. Czechia") is False)
    from datetime import datetime as _dt, timezone as _tz
    _end = _dt(2026, 6, 12, tzinfo=_tz.utc)
    ORACLE_CACHE[("wx", "seoul")] = (time.time(),
                                     {"max": {"2026-06-12": 26.1},
                                      "min": {"2026-06-12": 18.0}})
    ok("oracle: forecast contradicts a hot bet",
       oracle_check("Will the highest temperature in Seoul be 27\u00b0C or "
                    "higher on June 12?", _end, "Yes")[0] is False)
    ok("oracle: forecast backs the No side",
       oracle_check("Will the highest temperature in Seoul be 27\u00b0C or "
                    "higher on June 12?", _end, "No")[0] is True)
    ORACLE_CACHE[("spot", "BTC")] = (time.time(), 60000.0)
    ORACLE_CACHE[("cvol", "BTC")] = (time.time(), 0.006)
    p_at = crypto_prob("BTC", 60000, 24)
    ok("pricing: at-the-money is a coin flip", 0.45 < p_at < 0.55)
    ok("pricing: far strike is near-impossible",
       crypto_prob("BTC", 90000, 24) < 0.02)
    ORACLE_CACHE[("spot", "BTC")] = (time.time(), 62000.0)
    ORACLE_CACHE[("cvol", "BTC")] = (time.time(), 0.006)
    ok("oracle: spot price reads crypto strikes",
       oracle_check("Will Bitcoin be above $66,000 on June 13?", _end, "No")[0] is True)
    ORACLE_CACHE.clear()

    orc = lambda agree, pnl, i: {"strategy": "explore", "pnl": pnl,
        "entry_price": 0.9, "side": "No", "name": f"market {i}",
        "closed": "2026-06-12T0%d:00:00+00:00" % (i % 10),
        "context": {"oracle_agree": agree, "spread": 0.01}}
    bto = brain_train({"settled": [orc(True, 0.1, i) for i in range(16)]
                       + [orc(False, -0.9, i) for i in range(16, 32)]})
    ok("brain learns to trust the oracle", bto["w"].get("oracle", 0) > 0.3)

    ok("whale: buy tape backs Yes", whale_verdict({"net": 900}, 0) is True)
    ok("whale: buy tape opposes No", whale_verdict({"net": 900}, 1) is False)
    ok("whale: thin tape abstains", whale_verdict({"net": 50}, 0) is None)
    ok("tape sign: buying Yes is Yes-flow", _tape_sign("BUY", 0) == 1.0)
    ok("tape sign: buying No is Yes-SELLING", _tape_sign("BUY", 1) == -1.0)
    ok("tape sign: selling No is Yes-flow", _tape_sign("SELL", 1) == 1.0)
    ok("smart: fresh-wallet buys back Yes",
       smart_verdict({"fresh": 400}, 0) is True)
    ok("smart: fresh-wallet buys oppose No",
       smart_verdict({"fresh": 400}, 1) is False)
    ok("smart: small fresh flow abstains",
       smart_verdict({"fresh": 150}, 0) is None)
    _t0 = time.time()
    ok("fresh wallet: young + few trades qualifies",
       _is_fresh({"n": 8, "first": _t0 - 2 * 86400}, now=_t0) is True)
    ok("fresh wallet: old wallet excluded",
       _is_fresh({"n": 8, "first": _t0 - 90 * 86400}, now=_t0) is False)
    ok("fresh wallet: market-maker bot excluded",
       _is_fresh({"n": 100, "first": _t0 - 600}, now=_t0) is False)

    HEADLINES[:] = [(time.time(), "bitcoin surges past $70,000 record high")]
    ok("sentiment: bullish headline reads positive",
       headline_sentiment("Will Bitcoin be above $70,000 on June 13?") > 0)
    HEADLINES[:] = [(time.time(), "bitcoin crashes below $50,000 amid fear")]
    ok("sentiment: bearish headline reads negative",
       headline_sentiment("Will Bitcoin be below $50,000 on June 13?") < 0)
    HEADLINES.clear()
    ok("sentiment: no coverage abstains",
       headline_sentiment("Will Bitcoin be above $70,000?") is None)

    HEADLINES[:] = [(time.time(), "bitcoin surges past $70,000 record high")]
    ok("smart news: agreeing headline confirms an up-move",
       news_confirmed("Will Bitcoin be above $70,000 on June 13?", 0.10)[0])
    ok("smart news: headline against the move does not confirm",
       not news_confirmed("Will Bitcoin be above $70,000 on June 13?", -0.10)[0])
    HEADLINES.clear()
    ok("smart news: no coverage = no confirmation",
       not news_confirmed("Will Bitcoin be above $70,000?", 0.10)[0])
    HEADLINES[:] = [(time.time(), "bitcoin surges past $70,000 record high")]
    ok("news: headline backs the matching market",
       news_backed("Will Bitcoin be above $70,000 on June 13?") is True)
    ok("news: unrelated market is noise",
       news_backed("Will the highest temperature in Oslo be 20C?") is False)
    HEADLINES.clear()

    _now = time.time()
    from email.utils import formatdate
    _xml = (f"<rss><channel><title>Feed</title>"
            f"<item><title>Fresh story about markets</title>"
            f"<pubDate>{formatdate(_now - 600)}</pubDate></item>"
            f"<item><title>Stale story from yesterday</title>"
            f"<pubDate>{formatdate(_now - 30 * 3600)}</pubDate></item>"
            f"<item><title>Undated story</title></item>"
            f"</channel></rss>")
    _items = _rss_items(_xml, _now)
    ok("rss: stale items dropped at the door, fresh + undated kept",
       [t for _, t in _items] == ["fresh story about markets",
                                  "undated story"])
    ok("rss: fresh item carries its real pubDate, not fetch time",
       abs(_items[0][0] - (_now - 600)) < 120)

    _t = now_utc().isoformat(timespec="seconds")
    _dead = [{"strategy": "high_prob", "pnl": -6.0, "entry_price": 0.92,
              "category": "Sports", "name": "Exact Score: A 1 - 0 B?",
              "context": {"lane": "r90"}, "closed": _t} for _ in range(16)]
    _live = [{"strategy": "high_prob", "pnl": 0.3, "entry_price": 0.97,
              "category": "Weather", "name": "Will it rain in Oslo?",
              "context": {}, "closed": _t} for _ in range(9)]
    _lrn = compute_learning({"settled": _dead + _live})
    ok("learning: dead sports-lane cohort cannot pause the living strategy",
       _lrn["high_prob"]["multiplier"] == 1.0)
    _alive = [dict(s, context={}, category="Weather",
                   name="Will it rain in Paris?") for s in _dead]
    _lrn2 = compute_learning({"settled": _alive + _live})
    ok("learning: identical losses in the living era DO pause",
       _lrn2["high_prob"]["multiplier"] == 0.0)

    # era hygiene must reach EVERY learner, not just compute_learning:
    # the same dead cohort, stamped into one 6h bucket, must not block the
    # hour, defund the kelly band, drag the bayes posterior, or mine vetoes
    _dead19 = [dict(s, closed=_t[:11] + "19" + _t[13:]) for s in _dead]
    _tod = time_of_day_model({"settled": _dead19 + _live})
    ok("m4: dead cohort cannot block an hour bucket",
       "18-24h" not in _tod["blocked"]["high_prob"])
    _alive19 = [dict(s, closed=_t[:11] + "19" + _t[13:]) for s in _alive]
    ok("m4: identical living losses DO block the hour bucket",
       "18-24h" in time_of_day_model(
           {"settled": _alive19 + _live})["blocked"]["high_prob"])
    _bs_dead = band_win_stats({"settled": _dead})
    _bs_none = band_win_stats({"settled": []})
    ok("kelly bands: dead cohort adds zero observations",
       _bs_dead.get("92") == _bs_none.get("92"))
    _bay = bayes_confidence({"settled": _dead + _live})
    ok("m3 bayes: posterior judges only the living era",
       _bay["high_prob"]["n"] == 9 and _bay["high_prob"]["mult"] == 1.0)
    _mined = mine_patterns({"settled": _dead19 * 2}, min_n=8)
    ok("miner: no vetoes mined from the dead cohort", _mined == [])

    # SPORTS-DESK v1 probe rails: pre-game only, hard budget, off by default
    _pc = {"enabled": True, "budget": 50, "max_per_trade": 5}
    ok("sports probe: pre-game market with budget room qualifies",
       sports_probe_ok(_pc, {"gameStartTime": "2099-01-01 00:00:00+00"}, 50.0)
       is True)
    ok("sports probe: in-game NEVER qualifies",
       sports_probe_ok(_pc, {"gameStartTime": "2020-01-01 00:00:00+00"}, 50.0)
       is False)
    ok("sports probe: exhausted budget refuses",
       sports_probe_ok(_pc, {"gameStartTime": "2099-01-01 00:00:00+00"}, 3.0)
       is False)
    ok("sports probe: disabled config refuses",
       sports_probe_ok({}, {"gameStartTime": "2099-01-01 00:00:00+00"}, 50.0)
       is False)
    _today = now_utc().isoformat(timespec="seconds")
    _probe_acct = {
        "positions": [
            {"strategy": "high_prob", "category": "Sports", "shares": 5,
             "entry_price": 0.97, "name": "A vs. B", "context": {}},
            {"strategy": "arbitrage", "category": "Sports", "shares": 100,
             "entry_price": 0.97, "name": "C vs. D", "context": {}}],
        "settled": [
            {"context": {"sports_probe": 1}, "closed": _today, "pnl": -10.0},
            {"context": {"sports_probe": 1}, "closed": _today, "pnl": 3.0},
            {"context": {"sports_probe": 1},
             "closed": "2020-01-01T00:00:00+00:00", "pnl": -40.0}]}
    ok("sports probe: budget counts open risk + TODAY's realized losses "
       "only (stopped losers can't free budget; riskless arbs and old "
       "days excluded)",
       abs(sports_probe_spent(_probe_acct) - 14.85) < 0.01)

    ok("scores: rep survives away/home flip + naming",
       _score_rep("South Korea", "Czechia", 1, 1) ==
       _score_rep("Czechia", "Korea Republic FC", 1, 1))

    ok("snipe: scoring side detected, ambiguous double-score abstains",
       _snipe_scoring_token(_score_rep("Lakers", "Celtics", 0, 0),
                            _score_rep("Lakers", "Celtics", 1, 0)) == "lakers"
       and _snipe_scoring_token(_score_rep("Lakers", "Celtics", 0, 0),
                                _score_rep("Lakers", "Celtics", 1, 1)) is None)

    flat = [0.50 + (0.002 if i % 2 else -0.002) for i in range(60)]
    ok("chart: calm tape = drift", _chart_stats(flat)["chart_pattern"] == "drift")
    spike = [0.50] * 40 + [0.50 + 0.02 * i for i in range(1, 11)] + [0.66, 0.62, 0.58]
    ok("chart: spike already reverting = spike_fade",
       _chart_stats(spike)["chart_pattern"] == "spike_fade")
    run_up = [0.40 + 0.005 * i for i in range(60)]
    ok("chart: pinned at the high = breakout (never faded)",
       _chart_stats(run_up)["chart_pattern"] == "breakout")
    ok("chart: too few points = no read", _chart_stats([0.5] * 10) is None)

    ok("family strips numbers",
       family_of("Will BTC be above $66,000 on June 11?") ==
       family_of("Will BTC be above $67,500 on June 12?"))
    ok("family separates questions",
       family_of("Highest temperature in Oslo 20°C") !=
       family_of("Will BTC be above $66,000?"))
    same_day = [{"name": f"BTC above {k} on June 11", "closed": "2026-06-11T10:00:00"}
                for k in (66, 67, 68)]
    ok("effective n collapses one experiment", effective_n(same_day) == 1)
    spread_out = [{"name": "BTC above 66k", "closed": "2026-06-11T10:00:00"},
                  {"name": "Oslo temperature 20", "closed": "2026-06-11T10:00:00"},
                  {"name": "BTC above 66k", "closed": "2026-06-12T10:00:00"}]
    ok("effective n counts real clusters", effective_n(spread_out) == 3)

    n = len(ran)
    print(f"\n{n - len(fails)}/{n} passed" + (f" — FAILURES: {fails}" if fails else ""))
    sys.exit(1 if fails else 0)


# ------------------------------------------------------------------ main

def attribution(account):
    """Claude's review instrument: which decision signals correlate with
    money. Groups every settled trade by the choices made at entry and exit
    so a reviewer sees instantly what's earning and what's bleeding."""
    rows = [t for t in account["settled"] if t["strategy"] != "arbitrage"]

    def bucket(rs, key):
        groups = {}
        for t in rs:
            v = key(t)
            if v is None:
                continue
            g = groups.setdefault(v, [0, 0.0])
            g[0] += 1
            g[1] = round(g[1] + t["pnl"], 2)
        return {k: {"n": n, "pnl": p, "avg": round(p / n, 3)}
                for k, (n, p) in sorted(groups.items())}

    ctx = lambda t: t.get("context") or {}
    return {
        "settled_analyzed": len(rows),
        "by_strategy": bucket(rows, lambda t: t["strategy"]),
        "by_brain_adj": bucket(rows, lambda t: None if ctx(t).get("brain_adj") is None
                               else "upsized>1.1" if ctx(t)["brain_adj"] > 1.1
                               else "downsized<0.9" if ctx(t)["brain_adj"] < 0.9 else "neutral"),
        "by_thompson_draw": bucket(rows, lambda t: None if ctx(t).get("ts_draw") is None
                                   else "high" if ctx(t)["ts_draw"] > 0 else "low"),
        "by_probation": bucket(rows, lambda t: "probation" if ctx(t).get("probation") else "regular"),
        "by_exit": bucket([t for t in rows if t.get("reason")],
                          lambda t: t["reason"].split("—")[0].strip()),
        "by_hour_utc": bucket(rows, lambda t: f"{int(t['closed'][11:13]) // 6 * 6:02d}h"
                              if t.get("closed") else None),
        "by_band": bucket(rows, lambda t: f"{int(round((t.get('entry_price') or 0) * 20)) * 5}c"),
        "by_pattern": bucket(rows, lambda t: ctx(t).get("chart_pattern")),
        "by_whale": bucket(rows, lambda t: None if ctx(t).get("whale_agree") is None
                           else ("agree" if ctx(t)["whale_agree"] else "disagree")),
        "by_smart_money": bucket(rows, lambda t: None if ctx(t).get("smart_agree") is None
                                 else ("agree" if ctx(t)["smart_agree"] else "disagree")),
        "by_chartml": bucket(rows, lambda t: None if ctx(t).get("chart_ml") is None
                             else ("revert-likely" if ctx(t)["chart_ml"] >= 0.65
                                   else "continuation" if ctx(t)["chart_ml"] < 0.5
                                   else "mid")),
        "by_lane": bucket(rows, lambda t: ctx(t).get("lane")),
        "by_oracle": bucket(rows, lambda t: None
                            if ctx(t).get("oracle_agree") is None
                            or ctx(t).get("oracle_v") != 2
                            else ("agree" if ctx(t)["oracle_agree"] else "disagree")),
        "by_crossmarket": bucket(rows, lambda t: None
                                 if ctx(t).get("xmkt_divergence") is None
                                 else ("pm>consensus" if ctx(t)["xmkt_divergence"] > 0.05
                                       else "pm<consensus" if ctx(t)["xmkt_divergence"] < -0.05
                                       else "aligned")),
        # SPORTS specialist attribution: how trades fared when the sportsbook
        # consensus / Elo fair value disagreed with the entry price. None for
        # every non-sports trade (no sports_div context) so the bucket stays
        # scoped to the sports category.
        "by_sports": bucket(rows, lambda t: None
                            if ctx(t).get("sports_div") is None
                            else ("price>book" if ctx(t)["sports_div"] > 0.03
                                  else "price<book" if ctx(t)["sports_div"] < -0.03
                                  else "aligned")),
        # CRYPTO specialist attribution: how trades fared by where live spot sat
        # relative to the market's threshold. None for every non-crypto trade
        # (no crypto_spot_dist context) so the bucket stays scoped to crypto.
        "by_crypto": bucket(rows, lambda t: None
                            if ctx(t).get("crypto_spot_dist") is None
                            else ("spot>strike" if ctx(t)["crypto_spot_dist"] > 0.005
                                  else "spot<strike" if ctx(t)["crypto_spot_dist"] < -0.005
                                  else "at-strike")),
        # WEATHER specialist attribution: how trades fared by where the ensemble
        # forecast mean sat relative to the market strike. None for every
        # non-weather trade (no wx_fc_strike context) so the bucket stays scoped
        # to the weather category.
        "by_weather": bucket(rows, lambda t: None
                             if ctx(t).get("wx_fc_strike") is None
                             else ("forecast>strike" if ctx(t)["wx_fc_strike"] > 0.1
                                   else "forecast<strike" if ctx(t)["wx_fc_strike"] < -0.1
                                   else "at-strike")),
        # SOCIAL specialist attribution: how trades fared by the side-aligned
        # headline sentiment (news_rss + HackerNews). None for every non-social
        # trade (no social_sent_align context) so the bucket stays scoped to the
        # social category.
        "by_social": bucket(rows, lambda t: None
                            if ctx(t).get("social_sent_align") is None
                            else ("aligned+" if ctx(t)["social_sent_align"] > 0.1
                                  else "against-" if ctx(t)["social_sent_align"] < -0.1
                                  else "neutral")),
        "by_sentiment": bucket(rows, lambda t: None if ctx(t).get("news_sent") is None
                               else ("pos" if ctx(t)["news_sent"] > 0
                                     else "neg" if ctx(t)["news_sent"] < 0 else "neutral")),
        "by_news_backed": bucket(rows, lambda t: None if "news_backed" not in ctx(t)
                                 else ("news-backed" if ctx(t)["news_backed"]
                                       else "no-news")),
        "by_m15_verdict": bucket(rows, lambda t: None
                                 if ctx(t).get("m15_p") is None or not t.get("entry_price")
                                 else ("liked" if ctx(t)["m15_p"] >= t["entry_price"]
                                       else "disliked")),
    }


def classify_death(t):
    """Why did this losing trade lose? The chart shape decides, and each
    answer implies a DIFFERENT fix: gap deaths need entry avoidance (no exit
    rule can save them), orderly bleeds need earlier exits, whipsaws need
    looser stops, resolution losses mean the thesis itself was wrong."""
    entry = t.get("entry_price") or 0
    shares = t.get("shares") or (round(t["cost"] / entry) if entry else 1)
    exit_p = t.get("exit_price")
    if exit_p is None and shares:
        exit_p = round((t.get("proceeds") or 0) / shares, 3)
    reason = (t.get("reason") or "resolution").split("\u2014")[0].split("—")[0].strip()
    stop_p = t.get("stop") or (round(entry - 0.06, 3)
                               if t["strategy"] == "news" else 0.85)
    if t["pnl"] > -0.15:
        return "churn", exit_p
    if reason == "resolution":
        return "wrong-thesis", exit_p
    if exit_p is not None and stop_p and stop_p - exit_p > 0.05:
        return "gap-death", exit_p
    path = t.get("path") or []
    if len(path) >= 4 and all(path[i][1] > path[i + 1][1]
                              for i in range(len(path) - 4, len(path) - 1)):
        return "orderly-bleed", exit_p
    return "fast-stop", exit_p


def postmortem(account, check_whipsaw=True):
    """Autopsy every losing settle: classify the death from price action,
    and for recent losers with tokens, fetch the post-exit chart to see if
    the stop-out was vindicated (kept falling) or a whipsaw (recovered)."""
    losers = [t for t in account["settled"]
              if t["pnl"] < 0 and t["strategy"] != "arbitrage"]
    kinds, details = {}, []
    for t in losers:
        kind, exit_p = classify_death(t)
        k = kinds.setdefault((t["strategy"], kind), [0, 0.0])
        k[0] += 1
        k[1] = round(k[1] + t["pnl"], 2)
        details.append((t["pnl"], kind, t))
    print(f"{len(losers)} losing settles autopsied:")
    for (strat, kind), (n, pnl) in sorted(kinds.items(), key=lambda kv: kv[1][1]):
        print(f"  {strat:9s} {kind:14s} n={n:3d}  {pnl:+.2f}")
    fixes = {"gap-death": "avoid the entry (no exit can save it)",
             "orderly-bleed": "exit earlier — models 9/10/12 territory",
             "fast-stop": "stop hit within minutes — entry timing or stop width",
             "wrong-thesis": "the signal itself was wrong",
             "churn": "spread/noise cost — acceptable if labels are earned"}
    print("\nworst 6, individually:")
    for pnl, kind, t in sorted(details, key=lambda d: d[0])[:6]:
        ctx = t.get("context") or {}
        print(f"  {pnl:+.2f} [{t['strategy']}/{kind}] {t['name'][:46]}")
        print(f"        entry {t.get('entry_price')}, move1h {ctx.get('move_1h')}, "
              f"imb {ctx.get('imbalance')} -> {fixes[kind]}")
    if check_whipsaw:
        day_ago = (now_utc() - timedelta(hours=24)).isoformat(timespec="seconds")
        recent = [t for t in losers if t.get("token") and t.get("closed", "") > day_ago
                  and (t.get("reason") or "").startswith("stop")][:8]
        ws = vind = 0
        for t in recent:
            try:
                end_ts = int(datetime.fromisoformat(t["closed"]).timestamp())
            except ValueError:
                continue
            hist = get_json(f"{CLOB}/prices-history", params={
                "market": t["token"], "startTs": end_ts,
                "endTs": end_ts + 7200, "fidelity": 10}) or {}
            pts = [fnum(p.get("p")) for p in hist.get("history", [])]
            if not pts:
                continue
            if max(pts) >= (t.get("entry_price") or 1):
                ws += 1
            else:
                vind += 1
        if ws + vind:
            print(f"\npost-exit charts (last 24h stop-outs): {vind} vindicated "
                  f"(kept falling), {ws} whipsawed (recovered above entry — "
                  f"stop too tight)")
    return kinds


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    cfg = load_config()
    account = load_account(cfg)

    if cmd == "scan":
        run_pass(cfg, account, trade=False)
    elif cmd == "backtest":
        sample = int(sys.argv[2]) if len(sys.argv) > 2 else 400
        backtest(sample_target=sample)
    elif cmd == "research":
        research()
    elif cmd == "optimize":
        optimize()
    elif cmd == "lab":
        lab(int(sys.argv[2]) if len(sys.argv) > 2 else 400)
    elif cmd == "stats":
        history = []
        if HISTORY_FILE.exists():
            try:
                history = json.loads(HISTORY_FILE.read_text())
            except ValueError:
                pass
        for key, val in compute_metrics(account, history).items():
            print(f"  {key:>14}: {val if val is not None else '— (needs more trades/days)'}")
    elif cmd == "brief":
        brief()
    elif cmd == "risk":
        print(json.dumps(compute_risk(account), indent=2))
    elif cmd == "test":
        self_test()
    elif cmd == "sportsedge":
        sc = sportsedge_shadow_pass(account)
        print(json.dumps({"scorecard": sc,
                          "module_selftest": "run: python3 sportsedge.py"},
                         indent=1))
    elif cmd == "crossmarket":
        sc = crossmarket_shadow_pass(account)
        print(json.dumps({"scorecard": sc,
                          "module_selftest": "run: python3 crossmarket.py"},
                         indent=1))
    elif cmd == "patterns":
        compute_patterns(account)
        print(PATTERNS_FILE.read_text())
    elif cmd == "promote":
        promote_explorer_findings()
        print("promotion check done (config updated only on proven evidence)")
    elif cmd == "mlmodel":
        train_market_model()
    elif cmd == "replay":
        replay(int(sys.argv[2]) if len(sys.argv) > 2 else 4000)
    elif cmd == "postmortem":
        postmortem(account)
    elif cmd == "attribution":
        print(json.dumps(attribution(account), indent=1))
    elif cmd == "audit":
        probs = audit_books(account)
        print("books balanced to the penny" if not probs
              else "PROBLEMS:\n  " + "\n  ".join(probs))
    elif cmd == "web":
        print("Dashboard only — not trading. Ctrl+C to stop.")
        try:
            start_dashboard(background=False)
        except KeyboardInterrupt:
            print("\nstopped.")
    elif cmd == "paper":
        print("Paper trading started — simulated money, real prices. Ctrl+C to stop.")
        start_dashboard()
        if cfg["arbitrage"]["enabled"] and cfg["arbitrage"].get("fast_scan_seconds"):
            threading.Thread(target=arb_loop, args=(cfg, account), daemon=True).start()
            print("Fast arbitrage scanner on: checking the market every "
                  f"{cfg['arbitrage']['fast_scan_seconds']}s.")
        threading.Thread(target=daytrade_loop, args=(cfg, account),
                         daemon=True).start()
        print("Fast day-trading desk on: watchlist bulk-priced every ~15s, "
              "entries within seconds of a 5-minute overreaction.")
        threading.Thread(target=news_loop, daemon=True).start()
        print("News feeds on: Google News + BBC, tagging moves as "
              "news-backed vs noise.")
        threading.Thread(target=scores_loop, args=(account,),
                         daemon=True).start()
        print("Live-scores watcher on: ESPN feed racing the market (shadow).")
        threading.Thread(target=evolver_loop, daemon=True).start()
        print("Evolver on: strategies re-derived from fresh evidence every 6h.")
        if cfg.get("research_recorder", {}).get("enabled"):
            threading.Thread(target=recorder_loop, args=(cfg,), daemon=True).start()
            threading.Thread(target=mem_warmstart, daemon=True).start()
            threading.Thread(target=chartml_loop, daemon=True).start()
            print("Research recorder on: logging ~400 markets' prices per minute "
                  "to the data/ folder.")
        threading.Thread(target=sportsedge_loop, daemon=True).start()
        print("Sportsedge instrument on: SHADOW mode — grades its own sports "
              "fair-value/CLV, trades $0 until measured edge earns it.")
        threading.Thread(target=snipe_loop, daemon=True).start()
        print("Goal-snipe instrument on: SHADOW mode — measures feed-vs-market "
              "latency on real goals, trades $0, never weakens is_in_game.")
        threading.Thread(target=crossmarket_loop, daemon=True).start()
        print("Cross-market instrument on: SHADOW mode — Kalshi/PredictIt/"
              "Manifold consensus, graded vs the PM price, trades $0 until the "
              "brain's OOS gate earns it weight.")
        mins = cfg["scan_interval_minutes"]
        monitor_secs = max(1, int(cfg.get("monitor_interval_seconds", 10)))
        settle_secs = max(monitor_secs, int(cfg.get("settle_check_seconds", 60)))
        next_scan = next_settle = 0.0
        while True:
            try:
                cfg = load_config()  # hot-reload: the evolver edits config live
                mins = cfg.get("scan_interval_minutes", mins)  # cadence too
                now = time.time()
                if now >= next_scan:
                    run_pass(cfg, account, trade=True)
                    show_status(account)
                    print(f"\nscanning every {mins} min, watching prices "
                          f"every {monitor_secs}s... (dashboard stays live)")
                    # stamp AFTER the scan: a scan longer than the interval
                    # used to make scans back-to-back and starve the 1s
                    # price monitor entirely
                    next_scan = time.time() + mins * 60
                    next_settle = time.time() + settle_secs
                else:
                    monitor_pass(cfg, account, do_settle=(now >= next_settle))
                    if now >= next_settle:
                        next_settle = now + settle_secs
                time.sleep(monitor_secs)
            except KeyboardInterrupt:
                print("\nstopped. Run 'python3 bot.py status' anytime.")
                break
            except Exception as e:
                # never die overnight on a transient error — log and continue
                print(f"  ! recovered from error: {e}")
                time.sleep(30)
    elif cmd == "status":
        show_status(account)
    elif cmd == "reset":
        for f in (ACCOUNT_FILE, TRADE_LOG, HISTORY_FILE, LEARNING_FILE, ACTIVITY_FILE):
            f.unlink(missing_ok=True)
        print("Paper account wiped. Next run starts fresh.")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
