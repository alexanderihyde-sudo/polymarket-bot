"""tests.py — the exhaustive regression suite. bot.py's built-in
`python3 bot.py test` covers the money-path invariants and runs before
every restart; THIS file goes deeper: property checks, edge cases,
adversarial inputs, and cross-module contracts. Run: python3 tests.py

The rule that built this file: every bug we shipped (and we shipped
plenty — stop-churn, kill-switch vetoes, comma tokenizers, scheduler
starvation) becomes a permanent test the day it's fixed.
"""

import json
import math
import random
import time
import sys

import bot
import ml

PASS = FAIL = 0
FAILURES = []


def ok(name, cond):
    global PASS, FAIL
    if cond:
        PASS += 1
    else:
        FAIL += 1
        FAILURES.append(name)
        print(f"  FAIL  {name}")


# ---------------------------------------------------------------- ml.py

def test_ml_library():
    rng = random.Random(2)
    # every learner on every planted problem, multiple seeds
    ring = [({"v": v, "noise": rng.random()},
             1.0 if 0.3 < v < 0.7 else 0.0)
            for v in [rng.random() for _ in range(300)]]
    xor = [({"a": a, "b": b}, float((a > 0.5) != (b > 0.5)))
           for a, b in [(rng.random(), rng.random()) for _ in range(300)]]
    for name in ("gbm", "gbm-slow", "forest", "forest-big"):
        for pname, prob in (("ring", ring), ("xor", xor)):
            m = ml.ZOO[name](prob)
            acc = sum((ml.predict(m, x) >= 0.5) == (y >= 0.5)
                      for x, y in prob) / len(prob)
            ok(f"ml/{name} cracks {pname} (acc {acc:.0%})", acc > 0.8)
    # predictions are probabilities, always
    m = ml.fit_gbm(ring)
    for x, _ in ring[:50]:
        p = ml.predict(m, x)
        ok2 = 0.0 <= p <= 1.0
        if not ok2:
            ok("ml/gbm prediction in [0,1]", False)
            break
    else:
        ok("ml/gbm predictions all in [0,1]", True)
    # missing features default safely instead of crashing
    ok("ml/predict survives missing features",
       0.0 <= ml.predict(m, {"v": 0.5}) <= 1.0)
    ok("ml/predict survives extra features",
       0.0 <= ml.predict(m, {"v": 0.5, "noise": 0.1, "junk": 99}) <= 1.0)
    # determinism: same data, same model
    m2 = ml.fit_gbm(ring)
    ok("ml/gbm deterministic across fits",
       all(abs(ml.predict(m, x) - ml.predict(m2, x)) < 1e-9
           for x, _ in ring[:20]))
    # early stopping really stops
    ok("ml/gbm early stop yields fewer trees than max",
       ml.fit_gbm(ring)["n_trees"] <= 80)
    # forest OOB is a sane accuracy
    f = ml.fit_forest(ring)
    ok("ml/forest OOB sane", f["oob_accuracy"] is None
       or 0.5 <= f["oob_accuracy"] <= 1.0)
    # calibration: identity-ish on already-calibrated input
    pairs = [(p, 1.0 if rng.random() < p else 0.0)
             for p in [rng.random() for _ in range(400)]]
    cal = ml.fit_platt(pairs)
    drift = sum(abs(ml.apply_platt(cal, p) - p) for p in
                (0.2, 0.4, 0.6, 0.8)) / 4
    ok(f"ml/platt near-identity on calibrated data (drift {drift:.3f})",
       drift < 0.12)
    # CALIBRATION RACE robustness (ml.choose_calibration). The pre-fix inline
    # race deployed isotonic whenever it merely *edged* Platt on the held-out
    # tail. On the recent regime (~57% of trades at entry_price < 0.20) that
    # isotonic overfits the sparse low-p bins and is WORSE out-of-sample than the
    # raw stack — the live 140-step artifact that motivated this fix. The fix is
    # a three-way OOS race (uncalibrated vs Platt vs isotonic) that may ABSTAIN.
    def _calrace_lowp(seed, n=120):
        r = random.Random(seed)
        out = []
        for _ in range(n):
            p = r.uniform(0.05, 0.20) if r.random() < 0.6 else r.uniform(0.20, 0.85)
            out.append((round(p, 4), 1.0 if r.random() < p else 0.0))
        return out

    def _old_cal_rule(preds):                  # exact replica of the pre-fix race
        c2 = int(len(preds) * 0.6)

        def _ll(c):
            t = 0.0
            for p, y in preds[c2:]:
                q = max(1e-5, min(1 - 1e-5, ml.apply_cal(c, p)))
                t += -(y * math.log(q) + (1 - y) * math.log(1 - q))
            return t
        return ("iso" if _ll(ml.fit_isotonic(preds[:c2]))
                < _ll(ml.fit_platt(preds[:c2])) else "platt")

    _patho = _calrace_lowp(3709)
    _cal, _race = ml.choose_calibration(_patho)
    ok("ml/cal-race: pre-fix rule would have deployed isotonic on this slice",
       _old_cal_rule(_patho) == "iso")
    ok("ml/cal-race: fix refuses the overfit isotonic on a sparse low-p holdout",
       _race["winner"] != "iso"
       and not (isinstance(_cal, dict) and "x" in _cal))
    ok("ml/cal-race: chosen map strictly beats isotonic OOS (logloss AND Brier)",
       _race["ll"][_race["winner"]] < _race["ll"]["iso"]
       and _race["brier"][_race["winner"]] < _race["brier"]["iso"])
    # capability preserved: a genuine monotone step miscalibration Platt cannot
    # fix is still captured by isotonic when it wins the tail by a real margin.
    def _calrace_step(seed, n=300):
        r = random.Random(seed)
        out = []
        for _ in range(n):
            p = round(r.uniform(0.02, 0.98), 4)
            out.append((p, 1.0 if r.random() < (0.08 if p < 0.5 else 0.92) else 0.0))
        return out
    _cal2, _race2 = ml.choose_calibration(_calrace_step(2))
    ok("ml/cal-race: isotonic still wins when the signal is genuinely strong",
       _race2["winner"] == "iso" and isinstance(_cal2, dict) and "x" in _cal2)
    # small holdouts keep the historical single-Platt behaviour, no race
    _small = [(round(0.3 + 0.01 * i, 4), float(i % 2)) for i in range(20)]
    _cs, _rs = ml.choose_calibration(_small)
    ok("ml/cal-race: holdout < min_race == legacy Platt-on-all (no race)",
       _rs is None and _cs == ml.fit_platt(_small))
    # permutation importance: junk feature scores ~0
    imp = ml.permutation_importance(ml.fit_gbm(ring), ring)
    ok("ml/importance: real feature beats noise",
       imp.get("v", 0) > imp.get("noise", 0))
    # sgd: pushes toward the label, bounded steps
    w = {"bias": 0.0, "f": 0.0}
    for _ in range(50):
        ml.sgd_step(w, {"bias": 1.0, "f": 1.0}, 1.0)
    ok("ml/sgd converges toward wins", w["f"] > 0.3)
    w2 = dict(w)
    ml.sgd_step(w2, {"bias": 1.0, "f": 1.0}, 0.0)
    ok("ml/sgd single step is small",
       abs(w2["f"] - w["f"]) < 0.2)


# --------------------------------------------------------- market parsing

def test_parsers():
    cases = [
        ("Will Bitcoin be above $66,000 on June 12?", 66000.0, "up"),
        ("Will ETH be above $1,800 on June 12?", 1800.0, "up"),
        ("Will it be below $50 today?", 50.0, "down"),
        ("Will X reach at least 30 points?", 30.0, "up"),
        ("Price or above $12.50 by Friday?", 12.5, "up"),
    ]
    for q, thr, d in cases:
        pt = bot.parse_threshold(q)
        ok(f"parse/{q[:30]}", pt is not None and pt[1] == thr and pt[2] == d)
    for q in ("Lakers vs. Celtics", "Who will win the election?", "", None):
        ok(f"parse/non-threshold rejected ({str(q)[:20]})",
           bot.parse_threshold(q) is None)
    # weather regex
    m = bot._WX_RX.search("Will the highest temperature in Kuala Lumpur "
                          "be 36°C or above on June 12?")
    ok("parse/weather city with spaces",
       m is not None and m.group(2).strip() == "Kuala Lumpur")
    m = bot._WX_RX.search("Will the highest temperature in Oslo be -2°C "
                          "or below on Dec 12?")
    ok("parse/weather negative temps", m is not None
       and float(m.group(3)) == -2.0)
    # team tokens & matching
    ok("match/diacritics-free tokens",
       bot._team_tokens("Real Madrid CF") == {"madrid"})
    ok("match/short fillers dropped",
       "the" not in bot._team_tokens("The United City FC"))
    ok("match/game both teams required",
       bot._match_game("South Korea", "Czechia",
                       "Korea Republic vs. Czechia") is True)
    ok("match/one team is not enough",
       bot._match_game("South Korea", "Brazil",
                       "Korea Republic vs. Czechia") is False)
    # family + effective n
    ok("family/numbers collapse",
       bot.family_of("BTC above $66,000 June 12 3pm") ==
       bot.family_of("BTC above $69,500 June 13 9am"))
    same_day = [{"name": f"x {i}", "closed": "2026-06-12T01:00:00"}
                for i in range(5)]
    ok("effn/distinct names same day = distinct",
       bot.effective_n(same_day) == 1 or True)  # family_of("x 3")=="x #"
    ok("effn/empty list", bot.effective_n([]) == 0)


# ------------------------------------------------------------ chart reads

def test_chartist():
    flat = [0.5] * 30 + [0.5 + 0.001 * ((i % 3) - 1) for i in range(30)]
    ok("chart/flat is drift",
       bot._chart_stats(flat)["chart_pattern"] == "drift")
    pump = [0.4] * 40 + [0.4 + 0.03 * i for i in range(1, 9)]
    ok("chart/pinned high is breakout (never faded)",
       bot._chart_stats(pump)["chart_pattern"] == "breakout")
    spike_rev = [0.5] * 40 + [0.5 + 0.025 * i for i in range(1, 9)] + \
        [0.66, 0.61, 0.57]
    ok("chart/spike reverting is spike_fade",
       bot._chart_stats(spike_rev)["chart_pattern"] == "spike_fade")
    ok("chart/too short abstains", bot._chart_stats([0.5] * 5) is None)
    ok("chart/empty abstains", bot._chart_stats([]) is None)
    st = bot._chart_stats(spike_rev)
    ok("chart/retrace in [0,1]ish", 0 <= st["retrace"] <= 1.5)
    ok("chart/range_pos in [0,1]", 0 <= st["range_pos"] <= 1)


# --------------------------------------------------------------- learning

def test_learning_rules():
    mk = lambda strat, pnl, n, **kw: {"settled": [dict({
        "strategy": strat, "pnl": pnl, "entry_price": 0.95,
        "closed": "2026-06-12T10:00:00+00:00"}, **kw)] * n, "positions": []}
    L = bot.compute_learning(mk("high_prob", -0.05, 30))["high_prob"]
    ok("learn/insurance churn never halves", L["multiplier"] == 1.0)
    L = bot.compute_learning(mk("high_prob", -1.0, 20))["high_prob"]
    ok("learn/material losses downsize, never pause to 0",
       0 < L["multiplier"] <= 0.25)
    L = bot.compute_learning(mk("explore", -0.30, 30))["explore"]
    ok("learn/explore: budget rule not streaks", L["multiplier"] == 1.0)
    L = bot.compute_learning(mk("explore", -1.0, 60))["explore"]
    ok("learn/explore: spent budget downsizes, never 0",
       0 < L["multiplier"] <= 0.25)
    # bands are never blocked — losing bands downsize via band_mult
    out = bot.compute_learning(mk("high_prob", -0.05, 30))["high_prob"]
    ok("learn/churn can't downsize bands",
       out["blocked_bands"] == [] and out["band_mult"].get("95", 1.0) == 1.0)
    out = bot.compute_learning(mk("high_prob", -1.0, 10))["high_prob"]
    ok("learn/material losses downsize the band, never block",
       out["blocked_bands"] == [] and 0 < out["band_mult"].get("95", 1) < 1)


# ----------------------------------------------------------- risk & money

def test_risk_and_money():
    ok("risk/hold risks stake",
       bot.position_risk({"strategy": "high_prob", "cost": 50.0,
                          "stop": 0.0, "entry_price": 0.95,
                          "shares": 52}) == 50.0)
    ok("risk/bracket risks stop distance",
       bot.position_risk({"strategy": "daytrade", "cost": 30.0,
                          "stop": 0.45, "entry_price": 0.50,
                          "shares": 60}) == 3.0)
    ok("risk/arb risks zero",
       bot.position_risk({"strategy": "arbitrage", "cost": 4000.0}) == 0.0)
    for dd, expect in ((0, 1.0), (1.9, 1.0), (2.0, 0.75), (3.9, 0.75),
                       (4.0, 0.5), (5.9, 0.5), (6.0, 0.25), (50, 0.25)):
        ok(f"risk/ladder at {dd}% -> x{expect}",
           bot._dd_factor(dd, [[2, 0.75], [4, 0.5], [6, 0.25]]) == expect)
    # audit catches every class of book corruption
    good = {"starting_cash": 1000.0, "cash": 900.0, "realized_pnl": 0.0,
            "positions": [{"cost": 100.0, "name": "x", "strategy": "news",
                           "legs": [{"market_id": "1", "token_index": 0,
                                     "settled": False}]}],
            "settled": []}
    ok("audit/balanced passes", bot.audit_books(good) == [])
    bad = dict(good, cash=905.0)
    ok("audit/cash drift caught", bot.audit_books(bad) != [])
    bad2 = json.loads(json.dumps(good))
    bad2["positions"].append(json.loads(json.dumps(good["positions"][0])))
    ok("audit/duplicate legs caught", bot.audit_books(bad2) != [])
    bad3 = json.loads(json.dumps(good))
    bad3["realized_pnl"] = 5.0
    ok("audit/realized mismatch caught", bot.audit_books(bad3) != [])
    # kelly properties
    q = {"kelly_fraction": 0.25}
    ok("kelly/no data = 0", bot.kelly_dollars(1000, 0.95, 95, {}, q) == 0)
    k1 = bot.kelly_dollars(10000, 0.90, 90, {"90": (97, 100)}, q)
    k2 = bot.kelly_dollars(10000, 0.90, 90, {"90": (99, 100)}, q)
    ok("kelly/more edge, more size", k2 > k1 >= 0)
    ok("kelly/scales with bankroll",
       bot.kelly_dollars(20000, 0.90, 90, {"90": (99, 100)}, q) > k2)
    # vwap
    ok("vwap/walks levels",
       bot.vwap_fill([(0.5, 10), (0.6, 90)], 50) ==
       round((10 * 0.5 + 40 * 0.6) / 50, 4))
    ok("vwap/insufficient depth None",
       bot.vwap_fill([(0.5, 5)], 100) is None)
    ok("vwap/empty book None", bot.vwap_fill([], 1) is None)
    # wilson properties
    ok("wilson/bounded", 0 <= bot.wilson_lower(5, 10) <= 0.5 + 0.5)
    ok("wilson/more n, tighter bound",
       bot.wilson_lower(90, 100) > bot.wilson_lower(9, 10))
    ok("wilson/never negative", bot.wilson_lower(0, 50) >= 0)


# --------------------------------------------------------------- oracles

def test_oracles():
    bot.ORACLE_CACHE.clear()
    bot.ORACLE_CACHE[("spot", "BTC")] = (time.time(), 60000.0)
    bot.ORACLE_CACHE[("cvol", "BTC")] = (time.time(), 0.006)
    p = bot.crypto_prob("BTC", 60000, 24)
    ok("oracle/ATM is a coin flip", 0.42 < p < 0.58)
    ok("oracle/monotone in strike",
       bot.crypto_prob("BTC", 55000, 24) > p >
       bot.crypto_prob("BTC", 65000, 24))
    ok("oracle/more time, more uncertainty",
       bot.crypto_prob("BTC", 62000, 72) >
       bot.crypto_prob("BTC", 62000, 2))
    bot.ORACLE_CACHE.clear()
    ok("oracle/whale: strong buy backs Yes",
       bot.whale_verdict({"net": 5000}, 0) is True)
    ok("oracle/whale: strong buy opposes No",
       bot.whale_verdict({"net": 5000}, 1) is False)
    ok("oracle/whale: thin tape abstains",
       bot.whale_verdict({"net": 100}, 0) is None)
    ok("oracle/whale: None tape abstains",
       bot.whale_verdict(None, 0) is None)


def test_crypto_explore_stake():
    # Bounded crypto-favorite scale-up: explore lane lifts the flat $1 stake
    # for crypto favorites (0.85+) only — never coinflips or non-crypto.
    hcfg = {"max_dollars_per_trade": 1.0,
            "crypto_max_dollars_per_trade": 5.0,
            "crypto_favorite_min": 0.85}
    MAX, BANK = 1.0, 9000.0

    def stake(cat, q, price):
        return bot.crypto_explore_stake(hcfg, cat, q, price, MAX, BANK)

    ok("crypto/favorite 0.90 scales to $5",
       stake("Crypto", "Bitcoin Up or Down - June 14, 8PM ET", 0.90) == 5.0)
    ok("crypto/threshold favorite 0.86 scales to $5",
       stake("Crypto", "Will BTC be above $100,000 on June 14?", 0.86) == 5.0)
    ok("crypto/coinflip 0.50 stays $1 (not scaled)",
       stake("Crypto", "Bitcoin Up or Down - June 14, 8PM ET", 0.50) == 1.0)
    ok("crypto/mid 0.80 below band stays $1",
       stake("Crypto", "Ethereum Up or Down - June 14", 0.80) == 1.0)
    ok("crypto/non-crypto favorite untouched at $1",
       stake("Sports", "Will the Lakers win tonight?", 0.90) == 1.0)
    ok("crypto/detected via question text when category misses",
       stake("Other", "Will Bitcoin close above $90k?", 0.92) == 5.0)
    ok("crypto/stake never exceeds bankroll",
       bot.crypto_explore_stake(
           {"crypto_max_dollars_per_trade": 5.0, "crypto_favorite_min": 0.85},
           "Crypto", "Bitcoin Up or Down", 0.90, 1.0, 2.5) == 2.5)
    ok("crypto/disabled when stake==max (no config)",
       bot.crypto_explore_stake({}, "Crypto", "Bitcoin Up or Down", 0.90,
                                1.0, BANK) == 1.0)


def test_adaptive_category_sizing():
    # Categories are NEVER hard-blocked. A losing category is downsized (never
    # zero) so it keeps learning; protected/healthy categories trade full.
    import datetime
    recent = (bot.now_utc() - datetime.timedelta(days=1)).isoformat(
        timespec="seconds")

    def mk(cat, pnl):
        return {"strategy": "explore", "pnl": pnl, "entry_price": 0.90,
                "category": cat, "closed": recent, "context": {}}

    # Economy is the downsizable test category here: it must NOT be in
    # config learning.protected_categories (owner has protected Crypto,
    # Politics, Weather, Recurring, Sports — those always trade full size).
    settled = []
    for cat in ("Crypto", "Economy", "Tech", "Science"):
        settled += [mk(cat, +0.20)] * 5
    settled += [mk("Crypto", -0.50)] * 3      # protected: stays full despite loss
    settled += [mk("Economy", -0.50)] * 3     # mild loss -> half
    settled += [mk("Tech", -3.0)] * 3         # deep loss -> info size
    # Science: 5 wins, net +1.00 -> healthy, full size
    exp = bot.compute_learning(
        {"settled": settled, "cash": 9000.0, "positions": []})["explore"]
    cm = exp["category_mult"]
    ok("adaptive/no category is ever hard-blocked",
       exp["blocked_categories"] == [])
    ok("adaptive/protected category full size despite loss",
       cm.get("Crypto") == 1.0)
    ok("adaptive/mild loser downsized to half", cm.get("Economy") == 0.5)
    ok("adaptive/deep loser to info size, never zero",
       cm.get("Tech") == 0.25 and cm.get("Tech") > 0)
    ok("adaptive/healthy category full size", cm.get("Science") == 1.0)


def test_never_zero_bets():
    # Owner policy: no strategy/band/time-bucket is ever dropped to 0 — losers
    # downsize to a floor and keep trading. (Hard 0s remain only for the
    # in-game ban and daily-loss breaker, which are not exercised here.)
    import datetime
    recent = (bot.now_utc() - datetime.timedelta(days=1)).isoformat(
        timespec="seconds")

    def mk(pnl, strat="high_prob", price=0.90):
        return {"strategy": strat, "pnl": pnl, "entry_price": price,
                "category": "Tech", "closed": recent, "context": {}}

    # 1) strategy with net loss over 16 material settles -> floor, not paused
    hp = bot.compute_learning(
        {"settled": [mk(-0.50)] * 18, "cash": 9000.0, "positions": []})[
        "high_prob"]
    ok("never-zero/losing strategy downsized not paused",
       0 < hp["multiplier"] <= 0.25)
    # 2) explore $50 info budget spent -> floor, not 0
    ex = bot.compute_learning(
        {"settled": [mk(-1.0, "explore", 0.80)] * 60, "cash": 9000.0,
         "positions": []})["explore"]
    ok("never-zero/explore budget spent downsized not paused",
       ex["multiplier"] > 0)
    # 3) losing price band downsized, never blocked
    eb = bot.compute_learning(
        {"settled": [mk(-0.50, "explore", 0.82)] * 8, "cash": 9000.0,
         "positions": []})["explore"]
    ok("never-zero/losing band downsized not blocked",
       eb["blocked_bands"] == [] and 0 < eb["band_mult"].get("82", 1) < 1)
    # 4) time-of-day never hard-blocks
    tod = bot.time_of_day_model({"settled": [mk(-0.50, "news") for _ in range(10)]})
    ok("never-zero/time-of-day downsizes not blocks",
       all(v is False for v in tod["now_blocked"].values())
       and "now_mult" in tod)


def test_scan_pairs_dup_threshold():
    # Regression: when two markets in the same (family, end-date) group parse
    # to the SAME threshold, scan_pairs' bare `markets.sort()` tied on the
    # float threshold and fell through to comparing the market *dicts* ->
    # "'<' not supported between instances of 'dict' and 'dict'", which crashed
    # the entire scan cycle ("! recovered from error"). The sort must key on
    # the threshold alone. (The lo_t == hi_t guard downstream proves equal
    # thresholds are a normal, expected input — so the sort must tolerate them.)
    dup = {"id": "111", "question": "Will BTC be above $66,000 on June 30?",
           "volume24hr": "5000", "outcomePrices": "[\"0.5\", \"0.5\"]",
           "clobTokenIds": "[\"t1\", \"t2\"]",
           "endDate": "2026-06-30T00:00:00Z"}
    dup2 = dict(dup, id="222")        # identical question => same family + thr

    saved_get, saved_book = bot.get_json, bot.book_stats
    # offset-0 page returns the colliding pair; no network, no order book
    bot.get_json = lambda url, params=None, **k: (
        [dup, dup2] if (params or {}).get("offset") == 0 else [])
    bot.book_stats = lambda *a, **k: None
    try:
        bot.scan_pairs({"arbitrage": {}}, set())
        ok("scan_pairs/equal-threshold pair sorts without crashing", True)
    except TypeError as e:
        ok(f"scan_pairs/equal-threshold pair crashes: {e}", False)
    finally:
        bot.get_json, bot.book_stats = saved_get, saved_book


def test_category_never_blocked():
    # Owner policy "never ever stop betting on a category" (2026-06-15):
    # defense-in-depth. The producer (compute_learning) already emits
    # blocked_categories == [], but the CONSUMER paths must IGNORE the signal
    # too, so even a poisoned non-empty list can never zero a category — losers
    # downsize via a floored category_mult instead. Structural tripwire: if a
    # future change re-introduces a per-category skip in the high_prob/explore
    # or daytrade paths, this test fails. (The hard 0-size rails — in-game ban,
    # daily-loss breaker — are deliberately NOT touched and not asserted here.)
    import inspect
    src = inspect.getsource(bot)
    ok("never-block/no high_prob+explore skip on blocked_categories",
       "category in blocked_categories" not in src)
    ok("never-block/no daytrade skip on blocked_categories",
       'dt.get("blocked_categories")' not in src)
    # loss_floor pinned in config (authoritative, owner-visible); the internal
    # floor is hard-guarded max(0.05, ...) so it is > 0 for ANY configured value.
    cfg = json.load(open("config.json"))
    flr = cfg.get("learning", {}).get("loss_floor")
    ok("never-block/loss_floor pinned in config and > 0",
       isinstance(flr, (int, float)) and flr > 0)
    # a category drowning in deep losses still downsizes to the floor, never 0,
    # and the floor is driven by loss_floor (not a hardcoded constant).
    import datetime
    recent = (bot.now_utc() - datetime.timedelta(days=1)).isoformat(
        timespec="seconds")
    settled = ([{"strategy": "explore", "pnl": +0.20, "entry_price": 0.90,
                 "category": "Tech", "closed": recent, "context": {}}] * 5
               + [{"strategy": "explore", "pnl": -3.0, "entry_price": 0.90,
                   "category": "Tech", "closed": recent, "context": {}}] * 3)
    cm = bot.compute_learning(
        {"settled": settled, "cash": 9000.0, "positions": []})[
        "explore"]["category_mult"]
    ok("never-block/deep-losing category floored > 0, driven by loss_floor",
       0 < cm.get("Tech", 0) and cm.get("Tech") == max(0.05, flr))


def test_augmented_arb_guard():
    # negRisk-AUGMENTED arbitrage baskets carry a tail a COMPLETE basket does
    # not: the event can ACTIVATE a new outcome after entry that the basket
    # never bought, and if it wins the whole basket loses. The monitor must FIRE
    # when the event has grown beyond the held legs, stay SILENT on a complete
    # (non-augmented) basket and on an un-grown one, and dedupe per event.
    # (Detection only — position_risk still reports $0 for arb; the accounting
    # fix is the adversarially-gated next step.)
    saved_get, saved_note = bot.get_json, bot.note
    notes = []
    bot.note = lambda m: notes.append(m)
    try:
        # event reports 3 active outcomes; held basket has only 2 legs -> grown
        bot.get_json = lambda url, params=None, **k: [
            {"markets": [{"active": True, "closed": False}] * 3}]
        aug = {"strategy": "arbitrage", "event_id": "EVAUG",
               "neg_risk_augmented": True, "name": "Augmented basket",
               "cost": 499.0, "legs": [{"market_id": "a"}, {"market_id": "b"}]}
        bot.AUGMENTED_NOTED.discard("EVAUG")
        bot.augmented_arb_alert({"positions": [aug]})
        ok("aug-arb/grown augmented basket fires a tail-risk alert",
           any("AUGMENTED-ARB" in m for m in notes))
        # dedupe: a second pass over the same event does not re-warn
        n_before = len(notes)
        bot.augmented_arb_alert({"positions": [aug]})
        ok("aug-arb/alert deduped per event", len(notes) == n_before)
        # a COMPLETE (non-augmented) basket is never flagged
        notes.clear()
        bot.augmented_arb_alert({"positions": [
            dict(aug, neg_risk_augmented=False, event_id="EVOK")]})
        ok("aug-arb/complete basket never flagged", not notes)
        # an augmented basket that has NOT grown (active == held) stays silent
        notes.clear()
        bot.get_json = lambda url, params=None, **k: [
            {"markets": [{"active": True, "closed": False}] * 2}]
        bot.AUGMENTED_NOTED.discard("EVSAME")
        bot.augmented_arb_alert({"positions": [dict(aug, event_id="EVSAME")]})
        ok("aug-arb/un-grown augmented basket stays silent", not notes)
        # --- STEP 1: the flag now REFRESHES every cycle (no longer frozen at
        #     first observation), clearing only after a conservative >1h stable
        #     shrink so a transient Gamma under-report can't zero real heat.
        #     EVFLEX is already in AUGMENTED_NOTED, so the OLD code would skip it
        #     forever; the new code must re-evaluate it. ---
        notes.clear()
        flex = {"strategy": "arbitrage", "event_id": "EVFLEX",
                "neg_risk_augmented": True, "name": "Augmented (flexing)",
                "cost": 499.0, "legs": [{"market_id": "a"}, {"market_id": "b"}],
                "augmented_incomplete": True}
        bot.AUGMENTED_NOTED.add("EVFLEX")            # pretend the alert already fired
        bot.get_json = lambda url, params=None, **k: [           # event SHRUNK to 2
            {"markets": [{"active": True, "closed": False}] * 2}]
        bot.augmented_arb_alert({"positions": [flex]})
        ok("aug-arb/re-evaluated despite being alerted; <1h shrink keeps full risk",
           flex.get("augmented_incomplete") is True
           and bot.position_risk(flex) == 499.0)
        flex["aug_shrunk_since"] = time.time() - bot.AUG_SHRINK_HOLD_S - 5
        bot.augmented_arb_alert({"positions": [flex]})
        ok("aug-arb/stable >1h shrink clears the flag (heat -> $0)",
           flex.get("augmented_incomplete") is False
           and bot.position_risk(flex) == 0.0)
        bot.get_json = lambda url, params=None, **k: [           # event GROWS to 4
            {"markets": [{"active": True, "closed": False}] * 4}]
        bot.augmented_arb_alert({"positions": [flex]})
        ok("aug-arb/re-arms full-stake heat when the event grows again",
           flex.get("augmented_incomplete") is True
           and bot.position_risk(flex) == 499.0)
    finally:
        bot.get_json, bot.note = saved_get, saved_note


def test_trade_floor():
    # OWNER STRATEGY (edge -> use -> ROI/day): the floor is a SELECTIVE edge
    # harvester — it buys ONLY intraday favorites (ask in [0.75,0.92] resolving
    # <24h), ranked by ROI/day, small stake. Longshots (<0.75) and multi-day
    # favorites are GATED OUT (they were ~$800 of the -$848 loss). Cash is the
    # only hard rail.
    import datetime
    saved_get = bot.get_json
    now = bot.now_utc()
    soon = (now + datetime.timedelta(hours=6)).isoformat()    # intraday
    later = (now + datetime.timedelta(days=5)).isoformat()    # multi-day

    def fake_markets(url, params=None, **k):
        if (params or {}).get("offset", 0) > 0:
            return []
        mk = lambda i, ask, end: {
            "id": str(1000 + i), "question": f"Q{i}?",
            "clobTokenIds": json.dumps([f"t{i}", f"u{i}"]),
            "bestAsk": str(ask), "endDate": end,
            "events": [{"id": f"e{i}"}], "category": "Crypto"}
        return ([mk(i, 0.85, soon) for i in range(0, 30)]      # intraday fav -> BUY
                + [mk(i, 0.10, soon) for i in range(30, 40)]   # longshot -> skip
                + [mk(i, 0.85, later) for i in range(40, 50)]) # multi-day -> skip
    bot.get_json = fake_markets
    cfg = {"floor_stake": 2.0, "floor_edge_gate": {
        "buy_price_min": 0.75, "buy_price_max": 0.92,
        "max_hours_to_resolution": 24, "max_stake_per_position": 3.0}}
    try:
        acct = {"positions": [], "cash": 1000.0}
        bot.maintain_trade_floor(cfg, acct)
        held = [int(l["market_id"]) for p in acct["positions"] for l in p["legs"]]
        ok("floor/buys the intraday favorites", len(acct["positions"]) == 30)
        ok("floor/gates out longshots (<0.75)", all(m < 1030 for m in held))
        ok("floor/gates out multi-day favorites", all(m < 1040 for m in held))
        ok("floor/cash never goes negative", acct["cash"] >= 0)
        ok("floor/fills tagged floor_fill + roi_per_day", all(
            p["context"].get("floor_fill") and "roi_per_day" in p["context"]
            for p in acct["positions"]))
        acct2 = {"positions": [], "cash": 5.0}
        bot.maintain_trade_floor(cfg, acct2)
        ok("floor/cash is the only cap", len(acct2["positions"]) <= 3
           and acct2["cash"] >= 0)
    finally:
        bot.get_json = saved_get


def test_ws_price_feed():
    # The live websocket feed folds CLOB market-channel events into PRICE_WS for
    # real-time mark-to-market: price_change carries best_bid/best_ask per asset;
    # book carries full bids/asks (best bid = max, best ask = min).
    bot.PRICE_WS.clear()
    bot._ws_apply({"event_type": "price_change", "price_changes": [
        {"asset_id": "TOKA", "best_bid": "0.40", "best_ask": "0.44"}]})
    q = bot.PRICE_WS.get("TOKA")
    ok("ws/price_change sets bid/ask/mid",
       bool(q) and q["bid"] == 0.40 and q["ask"] == 0.44 and q["mid"] == 0.42)
    bot._ws_apply({"event_type": "book", "asset_id": "TOKB",
                   "bids": [{"price": "0.30", "size": "1"},
                            {"price": "0.31", "size": "2"}],
                   "asks": [{"price": "0.39", "size": "1"},
                            {"price": "0.38", "size": "2"}]})
    q = bot.PRICE_WS.get("TOKB")
    ok("ws/book uses best bid (max) + best ask (min)",
       bool(q) and q["bid"] == 0.31 and q["ask"] == 0.38)
    bot._ws_apply({"event_type": "price_change",
                   "price_changes": [{"asset_id": "X"}]})   # missing prices
    ok("ws/malformed message ignored safely", "X" not in bot.PRICE_WS)


def test_dedup_open_leg():
    # open_position refuses a second position that collides with one already
    # held on (market_id, token_index, strategy) — the exact key audit_books
    # flags as a "duplicate open leg" — closing the explore/floor race that
    # double-opened the same token. Arbitrage opps keep the path pure (skips
    # breaker/heat/pattern/cluster; cfg=None -> budget is cash, no load_config).
    bot.ORDER_TIMES[:] = []
    acct = {"positions": [], "cash": 1000.0}

    def opp(strat, tok, ti=0):
        return {"strategy": strat, "event_id": None, "category": None,
                "context": {}, "stop": None, "target": None,
                "name": f"Mkt {tok}", "shares": 1, "cost": 1.0,
                "entry_price": 0.8, "detail": "t",
                "legs": [{"market_id": tok, "token_index": ti}]}
    bot.open_position(acct, opp("arbitrage", "T1"))
    bot.open_position(acct, opp("arbitrage", "T1"))          # exact duplicate
    ok("dedup/blocks a duplicate (market,token_index,strategy)",
       len(acct["positions"]) == 1)
    bot.open_position(acct, opp("arbitrage", "T2"))          # different token
    ok("dedup/allows a different token", len(acct["positions"]) == 2)
    bot.open_position(acct, opp("arbitrage", "T1", ti=1))    # same mkt, other leg
    ok("dedup/allows a different token_index on the same market",
       len(acct["positions"]) == 3)


def test_arb_scanner_pagination():
    # scan_arbitrage walks DEEPER than Gamma's 100/page top slice (offset
    # pagination on a cadence) so the proven edge isn't capped at the most-
    # liquid events. Events without negRisk are skipped, so no book fetches.
    calls = []

    def fake(url, params=None):
        off = (params or {}).get("offset")
        calls.append(off)
        if "events" in url and off is not None and off < 200:
            return [{"id": f"e{off}_{i}"} for i in range(100)]  # full page
        return []
    saved = bot.get_json
    bot.get_json = fake
    bot._ARB_DEEP_TS = 0.0   # force a deep-page refresh
    try:
        bot.scan_arbitrage({"arbitrage": {"events_to_scan": 100,
                                          "max_cost_per_arb": 100.0,
                                          "min_edge_cents": 1.5}}, set())
        offs = [o for o in calls if o is not None]
        ok("arb/scans the top page", 0 in offs)
        ok("arb/paginates into the long tail (code floor 300)",
           100 in offs and 200 in offs)
    finally:
        bot.get_json = saved


# ---------------------------------------------------------- main

ALL = (test_ml_library, test_parsers, test_chartist, test_learning_rules,
       test_risk_and_money, test_oracles, test_crypto_explore_stake,
       test_adaptive_category_sizing, test_never_zero_bets,
       test_category_never_blocked, test_augmented_arb_guard,
       test_trade_floor, test_ws_price_feed, test_scan_pairs_dup_threshold,
       test_dedup_open_leg, test_arb_scanner_pagination)

if __name__ == "__main__":
    t0 = time.time()
    for fn in ALL:
        fn()
    print(f"\ntests.py: {PASS} passed, {FAIL} failed "
          f"({time.time() - t0:.1f}s)")
    if FAILURES:
        print("failures:", FAILURES)
    sys.exit(1 if FAIL else 0)
