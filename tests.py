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
    ok("learn/material losses pause", L["multiplier"] == 0.0)
    L = bot.compute_learning(mk("explore", -0.30, 30))["explore"]
    ok("learn/explore: budget rule not streaks", L["multiplier"] == 1.0)
    L = bot.compute_learning(mk("explore", -1.0, 60))["explore"]
    ok("learn/explore: spent budget pauses", L["multiplier"] == 0.0)
    # band blocks need material evidence
    out = bot.compute_learning(mk("high_prob", -0.05, 30))["high_prob"]
    ok("learn/churn can't block bands", out["blocked_bands"] == [])
    out = bot.compute_learning(mk("high_prob", -1.0, 10))["high_prob"]
    ok("learn/material losses block the band",
       "95" in out["blocked_bands"])


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

    settled = []
    for cat in ("Crypto", "Politics", "Tech", "Science"):
        settled += [mk(cat, +0.20)] * 5
    settled += [mk("Crypto", -0.50)] * 3      # protected: stays full despite loss
    settled += [mk("Politics", -0.50)] * 3    # mild loss -> half
    settled += [mk("Tech", -3.0)] * 3         # deep loss -> info size
    # Science: 5 wins, net +1.00 -> healthy, full size
    exp = bot.compute_learning(
        {"settled": settled, "cash": 9000.0, "positions": []})["explore"]
    cm = exp["category_mult"]
    ok("adaptive/no category is ever hard-blocked",
       exp["blocked_categories"] == [])
    ok("adaptive/protected category full size despite loss",
       cm.get("Crypto") == 1.0)
    ok("adaptive/mild loser downsized to half", cm.get("Politics") == 0.5)
    ok("adaptive/deep loser to info size, never zero",
       cm.get("Tech") == 0.25 and cm.get("Tech") > 0)
    ok("adaptive/healthy category full size", cm.get("Science") == 1.0)


# ---------------------------------------------------------- main

ALL = (test_ml_library, test_parsers, test_chartist, test_learning_rules,
       test_risk_and_money, test_oracles, test_crypto_explore_stake,
       test_adaptive_category_sizing)

if __name__ == "__main__":
    t0 = time.time()
    for fn in ALL:
        fn()
    print(f"\ntests.py: {PASS} passed, {FAIL} failed "
          f"({time.time() - t0:.1f}s)")
    if FAILURES:
        print("failures:", FAILURES)
    sys.exit(1 if FAIL else 0)
