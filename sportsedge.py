"""sportsedge.py — a self-grading sports fair-value & closing-line-value
instrument for the Polymarket paper bot.

IT DEFAULTS TO NO BET. It runs in SHADOW: it records a fair-value estimate
and an edge for every modelable sports market, then grades its own
closing-line value (CLV) and calibration on realized outcomes — and it
sizes ZERO until those out-of-sample numbers earn it the right. It never
trusts a backtest, because the bot's own history is a monument to a
backtester that lied: the sports research instrument said 90-99c favorites
won 75/75 across 61 families, and the live lane went 0/5 (-$50.56) the same
day (H#9). Backtest performance therefore NEVER authorizes sizing here.

Honest priors, measured 2026-06-12 over 723 settled markets:
  * Pre-game sports prices are near-efficient — Brier collapses from 0.064
    at entry to 0.0001 at the close. A calibrated fair-value model has
    almost nothing to harvest pre-game against a ~1.5-3c round-trip cost.
  * The one structurally-defensible edge is LIVE cross-source latency
    (ESPN reflects a score before the thin Polymarket book reprices) — but
    that edge is, as of build time, entirely UNMEASURED (0 captures). So
    this module MEASURES it read-only and bets nothing until 15+ events
    show the post-event move beats the at-jump spread out-of-sample.

Contract mirrors ml.py / chartml.py: pure Python, imports only ml,
deterministic, self-proving on planted problems before it is trusted.
"""

import math
import re

import ml

# Whitelist: leagues with a real ESPN scoreboard the bot can join against.
# Moneyline / match-winner only in v1 (cleanest two-outcome book, 1c
# spreads on MLB). Everything else abstains.
SCORE_LEAGUES = ("MLB", "NBA", "WNBA", "NFL", "NHL", "FIFA-WC")
WINPROB_LEAGUES = ("MLB", "NBA", "WNBA", "NFL")   # native ESPN win-prob feed

_FUTURES_RX = re.compile(
    r"champion|championship|\bwin the\b|\baward\b|mvp|to make|to reach"
    r"|season|series winner|group |advance|qualif|finals\b|cup winner",
    re.I)
_PROP_RX = re.compile(
    r"first to|exact score|both teams|total .* (over|under)|how many"
    r"|margin|player|mvp|assists|rebounds|home run|strikeout|\bprop\b",
    re.I)
# city tokens are NOT distinctive — two New York teams share "new york".
# A real join needs a non-city token (team nickname) in common.
_CITY_TOKENS = {
    "new", "york", "los", "angeles", "san", "bay", "city", "united",
    "fc", "sc", "club", "chicago", "boston", "miami", "dallas", "houston",
    "denver", "phoenix", "atlanta", "seattle", "toronto", "saint", "st",
    "north", "south", "east", "west", "state", "the", "of", "and",
}
_STOP = _CITY_TOKENS | {"vs", "v", "at", "moneyline", "ml", "win", "to",
                        "game", "match", "or"}


# --------------------------------------------------- name join / whitelist


def _tok(name):
    """Distinctive lowercase tokens of a team name (nicknames, not cities)."""
    raw = re.sub(r"[^a-z0-9 ]", " ", (name or "").lower()).split()
    return {w for w in raw if len(w) >= 3 and w not in _STOP}


def join_event(market, espn_board):
    """Match a Polymarket sports market to exactly one ESPN board event,
    or None. The board is a list of dicts with 'home','away','date','state'
    (state in pre|in|post) and 'id'. Honest by construction: a futures /
    prop / non-whitelist / not-on-today's-board / same-city-ambiguous /
    date-misaligned market returns None and is never priced.

    The market dict needs: 'question', 'date' (YYYY-MM-DD, the game day),
    'league' (one of SCORE_LEAGUES), 'outcomes' (the two team labels)."""
    q = market.get("question") or ""
    if _FUTURES_RX.search(q) or _PROP_RX.search(q):
        return None
    if market.get("league") not in SCORE_LEAGUES:
        return None
    outs = market.get("outcomes") or []
    if len(outs) != 2:
        return None                       # two-outcome moneyline only
    a_tok, b_tok = _tok(outs[0]), _tok(outs[1])
    if not a_tok or not b_tok or (a_tok & b_tok):
        return None                       # unnamed or self-colliding
    day = market.get("date")
    cands = []
    for ev in espn_board:
        if ev.get("date") != day:
            continue                      # date filter: never cross days
        h, a = _tok(ev.get("home")), _tok(ev.get("away"))
        # each market outcome must claim a DISTINCT side by a non-city token
        sides = {0: bool(a_tok & h), 1: bool(b_tok & a),
                 2: bool(a_tok & a), 3: bool(b_tok & h)}
        if (sides[0] and sides[1]) or (sides[2] and sides[3]):
            cands.append(ev)
    if len(cands) != 1:                    # 0 = not on board; >1 = ambiguous
        return None
    return cands[0]


# -------------------------------------------------------- Elo fair value


def elo_expect(r_a, r_b, home_adv=0.0):
    """Logistic Elo win probability of A over B (home_adv added to A)."""
    return 1.0 / (1.0 + 10.0 ** (-((r_a + home_adv) - r_b) / 400.0))


def elo_update(ratings, a, b, a_won, k=20.0, home_adv=0.0):
    """Update ratings in place after a settled game (a_won in {0,1})."""
    ra, rb = ratings.get(a, 1500.0), ratings.get(b, 1500.0)
    exp_a = elo_expect(ra, rb, home_adv)
    ratings[a] = ra + k * (a_won - exp_a)
    ratings[b] = rb + k * ((1 - a_won) - (1 - exp_a))
    return ratings


def train_ratings(games, k=20.0, home_adv=35.0):
    """Walk games chronologically -> per-team Elo. games: list of
    {a, b, a_won, t} ascending by t. Returns {team: rating}."""
    ratings = {}
    for g in sorted(games, key=lambda x: x.get("t", 0)):
        elo_update(ratings, g["a"], g["b"], g["a_won"], k, home_adv)
    return ratings


# ------------------------------------------------------ live fair value


def live_fair_value(pre_p, espn_winprob, frac_elapsed):
    """Blend the pre-game prior with ESPN's live win-prob as the game
    progresses. Early on we trust the prior; late we trust the live feed.
    espn_winprob is None when the league has no native feed -> we ABSTAIN
    (return None) rather than re-arb sportsbook-implied odds (the H#9 trap).
    frac_elapsed in [0,1]."""
    if espn_winprob is None:
        return None
    w = max(0.0, min(1.0, frac_elapsed))      # weight on the live feed
    return (1 - w) * pre_p + w * espn_winprob


# ---------------------------------------------------------- fair value


def fair_value(state, feats):
    """Calibrated P(true) for the favored outcome, or None to abstain.
    feats: {league, a, b, home_adv, state(pre|in|post), espn_winprob,
            frac_elapsed}. Fit to REALIZED outcomes; ESPN win-prob is only
    a live blend weight, never the training target."""
    if feats.get("state") == "post":
        return None
    ratings = state.get("ratings") or {}
    ra = ratings.get(feats["a"], 1500.0)
    rb = ratings.get(feats["b"], 1500.0)
    pre_p = elo_expect(ra, rb, feats.get("home_adv", 0.0))
    if feats.get("state") == "in":
        p = live_fair_value(pre_p, feats.get("espn_winprob"),
                            feats.get("frac_elapsed", 0.0))
        if p is None:
            return None
    else:
        p = pre_p
    cal = state.get("cal")
    return ml.apply_cal(cal, p) if cal else max(0.001, min(0.999, p))


# ----------------------------------------------------- cost & edge verdict


def book_cost(ask_yes, ask_no):
    """True round-trip cost of a Polymarket two-outcome book: the overround
    (ask_yes + ask_no - 1), halved for a one-sided entry. There is no
    separate vig — outcomePrices normalize to ~1."""
    return max(0.0, (ask_yes + ask_no - 1.0) / 2.0)


# size_factor is the ONLY thing this module can do to a probe bet, and it
# lives in [0, 1] — it can SHRINK the $5 stake, never grow it, and it is 0
# until a bucket is promoted by the operator on measured CLV.
def edge_verdict(p_true, ask, cost, mode="shadow", size_factor=0.0,
                 safety_margin=0.01):
    """The bet decision. Returns a dict that DEFAULTS to no bet, size 0.
    A shadow/backtest verdict can NEVER return bet=True — only a verdict
    explicitly run in mode='live' with a positive promoted size_factor and
    edge beyond cost+margin may bet, and even then size_factor caps it."""
    if p_true is None:
        return {"bet": False, "size_factor": 0.0, "edge": None,
                "p_true": None, "reason": "abstain"}
    edge = p_true - ask - cost
    sf = max(0.0, min(1.0, size_factor))
    bet = (mode == "live" and sf > 0.0 and edge > safety_margin)
    return {"bet": bool(bet), "size_factor": sf if bet else 0.0,
            "edge": round(edge, 4), "p_true": round(p_true, 4),
            "reason": ("edge" if bet else
                       "no_edge" if edge <= safety_margin else
                       "shadow" if mode != "live" else "unpromoted")}


# ------------------------------------------------------- closing-line value


def clv(entry_price, closing_price, won):
    """Closing-line value of a settled bet, sign-correct: did we get a
    better price than the market's close? Positive CLV is the primary
    truth signal (the close is ~640x more informative than entry). We buy
    the favored side at entry_price; the close repriced to closing_price.
    CLV = closing_price - entry_price (we gain if the market moved our way)
    and we tag whether the bet actually won for calibration."""
    return {"clv": round(closing_price - entry_price, 4),
            "entry": entry_price, "close": closing_price, "won": int(won)}


# ----------------------------------------------------- self-grading


def grade(predictions):
    """Out-of-sample scorecard over settled shadow predictions. Each pred:
    {p_true, market_price, won, clv}. A champion is only adoptable if it
    beats BOTH the market's entry price AND the base rate on Brier, and its
    mean CLV is positive. Returns the scorecard; never a sizing decision."""
    n = len(predictions)
    if n == 0:
        return {"n": 0, "verdict": "no data"}
    base = sum(p["won"] for p in predictions) / n
    bs_model = bs_mkt = bs_base = 0.0
    for p in predictions:
        y = p["won"]
        bs_model += (p["p_true"] - y) ** 2
        bs_mkt += (p["market_price"] - y) ** 2
        bs_base += (base - y) ** 2
    bs_model, bs_mkt, bs_base = bs_model / n, bs_mkt / n, bs_base / n
    mean_clv = sum(p.get("clv", 0.0) for p in predictions) / n
    # ECE in 5 bins
    bins = {}
    for p in predictions:
        k = min(4, int(p["p_true"] * 5))
        b = bins.setdefault(k, [0, 0.0, 0.0])
        b[0] += 1
        b[1] += p["p_true"]
        b[2] += p["won"]
    ece = sum(c[0] / n * abs(c[1] / c[0] - c[2] / c[0])
              for c in bins.values())
    beats_market = bs_model < bs_mkt
    beats_base = bs_model < bs_base
    return {"n": n, "base_rate": round(base, 4),
            "brier_model": round(bs_model, 5),
            "brier_market": round(bs_mkt, 5),
            "brier_base": round(bs_base, 5),
            "mean_clv": round(mean_clv, 4), "ece": round(ece, 4),
            "beats_market": beats_market, "beats_base": beats_base,
            "verdict": ("adoptable" if (beats_market and beats_base
                        and mean_clv > 0) else "shadow-only")}


# ------------------------------------------------------ promotion gate


def promotion_ok(scorecard, bucket_ph, min_settles=15, ece_tol=0.05):
    """The bar to ever lift size_factor above 0 for a bucket — MEASURED,
    out-of-sample, manual-review only. Needs 15+ settles, positive CLV,
    calibrated within tolerance, beating the market, and no live drift
    alarm. Backtest numbers can NEVER satisfy this."""
    return bool(scorecard.get("n", 0) >= min_settles
                and scorecard.get("mean_clv", -1) > 0
                and scorecard.get("ece", 1) <= ece_tol
                and scorecard.get("beats_market")
                and (bucket_ph or {}).get("drifts", 0) == 0)


# --------------------------------------------------- live latency instrument


def latency_capture(score_ts, reprice_ts, pre_mid, post_mid, spread):
    """One read-only measurement of the live cross-source exploit: ESPN
    posted a score change at score_ts; the Polymarket book first repriced
    at reprice_ts. lag>0 means ESPN led. The exploitable move is the
    realized mid change MINUS the spread you'd cross to take it; net>0 over
    15+ events is the ONLY thing that may ever open a live lane."""
    lag = reprice_ts - score_ts
    move = post_mid - pre_mid
    return {"lag_s": round(lag, 2), "move": round(move, 4),
            "spread": round(spread, 4),
            "net": round(abs(move) - spread, 4),
            "espn_led": lag > 0}


def latency_summary(captures):
    """Aggregate the latency program. Stays SHADOW until n>=15 and the
    mean net (move beyond spread) is positive AND ESPN reliably leads."""
    n = len(captures)
    if n == 0:
        return {"n": 0, "ready": False, "reason": "no captures"}
    mean_net = sum(c["net"] for c in captures) / n
    led = sum(1 for c in captures if c["espn_led"]) / n
    mean_lag = sum(c["lag_s"] for c in captures) / n
    return {"n": n, "mean_net": round(mean_net, 4),
            "espn_led_frac": round(led, 3), "mean_lag_s": round(mean_lag, 2),
            "ready": bool(n >= 15 and mean_net > 0 and led >= 0.6),
            "reason": ("positive net, ESPN leads" if (n >= 15 and mean_net > 0
                       and led >= 0.6) else "insufficient/negative — stay shadow")}


# ------------------------------------------------------- training entry


def train_sportsedge(games, settled_shadow=None, home_adv=35.0):
    """Build the model state from realized games and (optionally) settled
    shadow predictions for calibration. JSON-serializable. games: list of
    {a,b,a_won,t}. settled_shadow: list of {p_true, won} for isotonic."""
    state = {"ratings": train_ratings(games, home_adv=home_adv),
             "home_adv": home_adv, "n_games": len(games)}
    sh = settled_shadow or []
    if len(sh) >= 30:
        state["cal"] = ml.fit_isotonic([(p["p_true"], p["won"]) for p in sh])
    else:
        state["cal"] = None
    return state


# --------------------------------------------------------- self-test


def _synth_league(rng, n_teams=12, n_games=600):
    """Planted league: each team has a hidden strength; stronger teams win
    more often (logistic). The Elo trainer must recover that ordering and
    the fair value must be calibrated against realized outcomes."""
    strengths = {f"t{i}": rng.gauss(0, 1) for i in range(n_teams)}
    games = []
    for g in range(n_games):
        a, b = rng.sample(list(strengths), 2)
        p = 1.0 / (1.0 + math.exp(-(strengths[a] - strengths[b])))
        a_won = 1 if rng.random() < p else 0
        games.append({"a": a, "b": b, "a_won": a_won, "t": g})
    return strengths, games


def self_test():
    import random
    rng = random.Random(7)
    strengths, games = _synth_league(rng)
    cut = int(len(games) * 0.7)
    state = train_sportsedge(games[:cut])
    # 1) Elo recovers the hidden strength ordering (rank correlation > 0)
    teams = sorted(strengths, key=lambda t: strengths[t])
    elos = [state["ratings"].get(t, 1500.0) for t in teams]
    concord = sum(1 for i in range(len(elos)) for j in range(i + 1, len(elos))
                  if elos[j] > elos[i])
    total = len(elos) * (len(elos) - 1) // 2
    ok1 = concord / total > 0.75
    print(f"  {'PASS' if ok1 else 'FAIL'}  Elo recovers planted strength "
          f"order (concordance {concord/total:.2f})")

    # 2) out-of-sample fair value beats the base rate on Brier
    preds = []
    for g in games[cut:]:
        p = fair_value(state, {"league": "MLB", "a": g["a"], "b": g["b"],
                               "home_adv": 0.0, "state": "pre"})
        preds.append({"p_true": p, "market_price": p, "won": g["a_won"],
                      "clv": 0.0})
    sc = grade(preds)
    ok2 = sc["brier_model"] < sc["brier_base"]
    print(f"  {'PASS' if ok2 else 'FAIL'}  OOS fair value beats base rate "
          f"(Brier {sc['brier_model']} < {sc['brier_base']})")

    # 3) edge_verdict NEVER bets in shadow, even with huge edge
    v = edge_verdict(0.99, 0.50, 0.0, mode="shadow", size_factor=1.0)
    ok3 = v["bet"] is False and v["size_factor"] == 0.0
    print(f"  {'PASS' if ok3 else 'FAIL'}  shadow mode can never bet "
          f"(bet={v['bet']}, size={v['size_factor']})")

    # 3b) even live mode refuses when edge <= cost+margin
    v2 = edge_verdict(0.52, 0.51, 0.02, mode="live", size_factor=1.0)
    ok3b = v2["bet"] is False
    # ...but bets when edge clears the bar in live+promoted
    v3 = edge_verdict(0.70, 0.50, 0.02, mode="live", size_factor=0.5)
    ok3b = ok3b and v3["bet"] is True and v3["size_factor"] == 0.5
    print(f"  {'PASS' if ok3b else 'FAIL'}  live bets only when edge>cost "
          f"AND promoted (size {v3['size_factor']})")

    # 4) whitelist rejects futures, props, off-board, same-city, wrong-day
    board = [{"home": "Yankees", "away": "Red Sox", "date": "2026-06-13",
              "state": "pre", "id": "1"}]
    base_mkt = {"question": "Yankees vs. Red Sox", "league": "MLB",
                "date": "2026-06-13", "outcomes": ["Yankees", "Red Sox"]}
    rej = [
        join_event({**base_mkt, "question": "Yankees to win the World Series"},
                   board) is None,                          # futures
        join_event({**base_mkt, "question": "Total runs over 8.5",
                    "outcomes": ["Over", "Under"]}, board) is None,  # prop
        join_event({**base_mkt, "date": "2026-06-14"}, board) is None,  # day
        join_event({**base_mkt, "league": "TENNIS"}, board) is None,    # lg
        join_event({**base_mkt, "outcomes": ["New York", "New York"]},
                   board) is None,                          # same-city
    ]
    acc = join_event(base_mkt, board) is not None           # the real game
    ok4 = all(rej) and acc
    print(f"  {'PASS' if ok4 else 'FAIL'}  whitelist rejects "
          f"futures/props/wrong-day/non-league/same-city, accepts the game")

    # 5) live fair value abstains without a native win-prob feed
    ok5 = (live_fair_value(0.6, None, 0.5) is None
           and live_fair_value(0.6, 0.8, 1.0) == 0.8)
    print(f"  {'PASS' if ok5 else 'FAIL'}  live FV abstains w/o ESPN "
          f"win-prob, trusts it late")

    # 6) isotonic calibration is monotonic
    cal = ml.fit_isotonic([(0.1, 0), (0.2, 0), (0.3, 1), (0.6, 0),
                           (0.7, 1), (0.9, 1)] * 6)
    xs = [ml.apply_cal(cal, x) for x in (0.1, 0.3, 0.5, 0.7, 0.9)]
    ok6 = all(xs[i] <= xs[i + 1] + 1e-9 for i in range(len(xs) - 1))
    print(f"  {'PASS' if ok6 else 'FAIL'}  isotonic calibration monotonic")

    # 7) CLV is sign-correct (favored bet, market moved our way = +CLV)
    c = clv(0.55, 0.80, won=1)
    ok7 = c["clv"] > 0 and clv(0.55, 0.30, won=0)["clv"] < 0
    print(f"  {'PASS' if ok7 else 'FAIL'}  CLV sign-correct "
          f"(+{c['clv']} when close beats entry)")

    # 8) latency program stays shadow until measured positive across 15+
    caps = [latency_capture(100.0, 102.0, 0.50, 0.55, 0.03)
            for _ in range(14)]
    not_ready = not latency_summary(caps)["ready"]
    caps += [latency_capture(100.0, 103.0, 0.50, 0.58, 0.02)
             for _ in range(6)]
    ready = latency_summary(caps)["ready"]
    ok8 = not_ready and ready
    print(f"  {'PASS' if ok8 else 'FAIL'}  latency lane stays shadow until "
          f"15+ net-positive ESPN-led captures")

    # 9) promotion gate refuses without measured CLV + calibration
    ok9 = (not promotion_ok({"n": 5, "mean_clv": 0.1, "ece": 0.01,
                             "beats_market": True}, {})
           and promotion_ok({"n": 20, "mean_clv": 0.02, "ece": 0.03,
                             "beats_market": True}, {"drifts": 0}))
    print(f"  {'PASS' if ok9 else 'FAIL'}  promotion needs 15+ settles, "
          f"+CLV, calibration, no drift")

    good = all([ok1, ok2, ok3, ok3b, ok4, ok5, ok6, ok7, ok8, ok9])
    print(f"\nsportsedge self-test: {'ALL PASS' if good else 'FAILURES'}")
    return good


if __name__ == "__main__":
    import sys
    sys.exit(0 if self_test() else 1)
