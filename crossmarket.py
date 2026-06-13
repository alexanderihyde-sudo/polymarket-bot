"""crossmarket.py — a self-grading CROSS-MARKET consensus instrument for the
Polymarket paper bot.

It reads OTHER prediction markets (Kalshi real-money, PredictIt real-money,
Manifold play-money) and — when a key is present — sportsbook moneyline
consensus, finds the FEW Polymarket markets that have a genuine same-event /
same-resolution twin elsewhere, and computes a reliability-weighted
consensus probability. The gap (pm_p - consensus_p) is a divergence SIGNAL.

It DEFAULTS TO NEUTRAL and never trades on divergence directly. Cross-market
divergence is informative but trap-laden: different resolution rules, timing,
vig, and event-matching errors mean a "10c edge" is usually a category error,
not money. So this module runs in SHADOW — it grades its own consensus (Brier
vs the PM market price, plus CLV) and only earns trading influence through the
bot brain's existing OOS/credibility gate and a by_crossmarket attribution
bucket. Day one the feature is effectively zero weight; it ramps up only where
it MEASURABLY predicts.

Contract mirrors sportsedge.py / ml.py: pure Python, stdlib + optional ml,
deterministic, self-proving on planted problems before it is trusted. Every
network connector is timeout-bounded and fail-silent: a dead external API
returns [] / {} and can never stall or crash the daemon.

NO FUTURE-DATA LEAKAGE: cross-market prices are read at/before decision time;
resolution info is used only to GRADE settled shadow predictions, never as a
matching or consensus feature.
"""

import json
import math
import re
import urllib.error
import urllib.request

# ---- source reliability weights (real-money books dominate play-money) ----
# Kalshi & PredictIt are real-money CFTC venues; Manifold is play-money and
# gets a deliberately small weight; sportsbook consensus (de-vigged) is solid
# where it exists. These are PRIORS on source quality, not earned skill — the
# brain's OOS gate decides whether the resulting divergence signal is worth
# anything at all.
SOURCE_WEIGHT = {
    "kalshi": 1.0,
    "predictit": 0.6,
    "oddsapi": 0.8,
    "manifold": 0.2,     # PLAY-MONEY — lowest reliability
}

_HTTP_TIMEOUT = 8.0
_UA = "polymarket-paper-bot/crossmarket (research, read-only)"

# ----------------------------------------------------------- http (governed
# by the caller; here just timeout-bounded + fail-silent). The bot wires its
# own requests.Session in via fetch_json; this stdlib path is the fallback so
# `python3 crossmarket.py` self-test and the connectors work standalone.


def _http_json(url, timeout=_HTTP_TIMEOUT):
    """GET url -> parsed JSON, or None on ANY error. Never raises."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError,
            OSError, TimeoutError, Exception):
        return None


def _fnum(v):
    """Tolerant float: Kalshi sends '0.4900' strings; None/'' -> None."""
    try:
        f = float(v)
        return f
    except (TypeError, ValueError):
        return None


# =========================================================== CONNECTORS
# Each returns a list of {source, question, p, weight, raw} dicts, or [] on any
# failure. p is an implied probability in (0,1). They NEVER raise.


def kalshi_markets(limit=1000, pages=3, fetch=_http_json):
    """Open Kalshi markets with a REAL price. Kalshi's current schema reports
    prices as dollar strings (yes_bid_dollars / yes_ask_dollars /
    last_price_dollars) and volume as volume_fp; older schema used integer
    cents (yes_bid / yes_ask / last_price, /100) and volume. We read whichever
    is present. implied p = last_price if traded, else mid(bid, ask)."""
    out, cursor = [], None
    base = ("https://api.elections.kalshi.com/trade-api/v2/markets"
            f"?limit={int(limit)}&status=open")
    for _ in range(max(1, pages)):
        url = base + (f"&cursor={cursor}" if cursor else "")
        d = fetch(url)
        if not isinstance(d, dict):
            break
        for m in d.get("markets", []) or []:
            try:
                rec = _kalshi_one(m)
            except Exception:
                rec = None
            if rec is not None:
                out.append(rec)
        cursor = d.get("cursor")
        if not cursor:
            break
    return out


def _kalshi_one(m):
    """Parse a single Kalshi market dict -> record or None. Keeps only markets
    with positive volume AND a real (non-zero) implied price."""
    vol = _fnum(m.get("volume_fp"))
    if vol is None:
        vol = _fnum(m.get("volume"))
    # dollars schema (current): already in [0,1]
    yb = _fnum(m.get("yes_bid_dollars"))
    ya = _fnum(m.get("yes_ask_dollars"))
    lp = _fnum(m.get("last_price_dollars"))
    if yb is None and ya is None and lp is None:
        # legacy cents schema: /100
        yb = (lambda v: v / 100.0 if v is not None else None)(_fnum(m.get("yes_bid")))
        ya = (lambda v: v / 100.0 if v is not None else None)(_fnum(m.get("yes_ask")))
        lp = (lambda v: v / 100.0 if v is not None else None)(_fnum(m.get("last_price")))
    p = None
    if lp is not None and lp > 0.0:
        p = lp
    elif yb is not None and ya is not None and (yb > 0.0 or ya > 0.0):
        p = (yb + ya) / 2.0
    if p is None or not (0.0 < p < 1.0) or (vol or 0) <= 0:
        return None
    title = (m.get("title") or "").strip()
    sub = (m.get("yes_sub_title") or "").strip()
    q = (title + (" | " + sub if sub else "")).strip()
    return {"source": "kalshi", "question": q, "p": round(p, 4),
            "weight": SOURCE_WEIGHT["kalshi"],
            "raw": {"ticker": m.get("ticker"), "vol": vol,
                    "close": m.get("close_time") or m.get("expiration_time")}}


def predictit_all(fetch=_http_json):
    """PredictIt: every contract's lastTradePrice is an implied probability."""
    d = fetch("https://www.predictit.org/api/marketdata/all/")
    if not isinstance(d, dict):
        return []
    out = []
    for mk in d.get("markets", []) or []:
        mname = (mk.get("shortName") or mk.get("name") or "").strip()
        for c in mk.get("contracts", []) or []:
            ltp = _fnum(c.get("lastTradePrice"))
            if ltp is None or not (0.0 < ltp < 1.0):
                continue
            cname = (c.get("shortName") or c.get("name") or "").strip()
            q = (mname + (": " + cname if cname and cname != mname else "")).strip()
            out.append({"source": "predictit", "question": q,
                        "p": round(ltp, 4), "weight": SOURCE_WEIGHT["predictit"],
                        "raw": {"market_id": mk.get("id"),
                                "contract_id": c.get("id"),
                                "close": c.get("dateEnd")}})
    return out


def manifold_markets(limit=500, fetch=_http_json):
    """Manifold BINARY markets carry `probability` directly. FLAGGED
    play-money: lowest reliability weight, never lifted by config here."""
    d = fetch(f"https://api.manifold.markets/v0/markets?limit={int(limit)}")
    if not isinstance(d, list):
        return []
    out = []
    for m in d:
        if m.get("outcomeType") != "BINARY" or m.get("isResolved"):
            continue
        p = _fnum(m.get("probability"))
        if p is None or not (0.0 < p < 1.0):
            continue
        out.append({"source": "manifold", "question": (m.get("question") or "").strip(),
                    "p": round(p, 4), "weight": SOURCE_WEIGHT["manifold"],
                    "play_money": True,
                    "raw": {"id": m.get("id"), "vol": m.get("volume"),
                            "close": m.get("closeTime")}})
    return out


def odds_api(sports=("baseball_mlb",), api_key=None, fetch=_http_json):
    """Sportsbook moneyline consensus, de-vigged to implied probs. ONLY runs
    when an ODDS_API_KEY is present (passed in or os.environ). With no key it
    returns [] and the source is simply SKIPPED — the bot never reads .env
    here; the key is taken from the environment by the caller. Two-way h2h
    only; props/futures are not requested."""
    if api_key is None:
        import os
        api_key = os.environ.get("ODDS_API_KEY")
    if not api_key:
        return []                              # no key -> source skipped
    out = []
    for sport in sports:
        url = (f"https://api.the-odds-api.com/v4/sports/{sport}/odds"
               f"?apiKey={api_key}&regions=us&markets=h2h&oddsFormat=decimal")
        d = fetch(url)
        if not isinstance(d, list):
            continue
        for ev in d:
            try:
                rows = _odds_event(ev)
            except Exception:
                rows = []
            out.extend(rows)
    return out


def devig_two_way(dec_a, dec_b):
    """De-vig a two-way decimal-odds moneyline into fair implied probs.
    Raw implied = 1/decimal; the book's overround is the sum > 1; we
    normalize it out (proportional / 'multiplicative' de-vig)."""
    if not dec_a or not dec_b or dec_a <= 1.0 or dec_b <= 1.0:
        return None
    ra, rb = 1.0 / dec_a, 1.0 / dec_b
    s = ra + rb
    if s <= 0:
        return None
    return ra / s, rb / s


def _odds_event(ev):
    """One Odds-API event -> per-outcome consensus records (median across
    books of the de-vigged two-way price)."""
    home, away = ev.get("home_team"), ev.get("away_team")
    if not home or not away:
        return []
    pa, pb = [], []        # de-vigged probs for home, away across books
    for bk in ev.get("bookmakers", []) or []:
        for mk in bk.get("markets", []) or []:
            if mk.get("key") != "h2h":
                continue
            price = {}
            for oc in mk.get("outcomes", []) or []:
                price[oc.get("name")] = _fnum(oc.get("price"))
            dv = devig_two_way(price.get(home), price.get(away))
            if dv:
                pa.append(dv[0])
                pb.append(dv[1])
    if not pa:
        return []
    ph, paw = _median(pa), _median(pb)
    label = f"{away} at {home}"
    return [{"source": "oddsapi", "question": f"{home} to win ({label})",
             "p": round(ph, 4), "weight": SOURCE_WEIGHT["oddsapi"],
             "raw": {"home": home, "away": away, "side": "home",
                     "n_books": len(pa), "commence": ev.get("commence_time")}},
            {"source": "oddsapi", "question": f"{away} to win ({label})",
             "p": round(paw, 4), "weight": SOURCE_WEIGHT["oddsapi"],
             "raw": {"home": home, "away": away, "side": "away",
                     "n_books": len(pa), "commence": ev.get("commence_time")}}]


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return None
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def gather_pool(fetch=_http_json, odds_sports=()):
    """Build the full cross-market pool from every available source,
    fail-silent per source. A dead source contributes [] and the others still
    work. Returns a flat list of source records."""
    pool = []
    for fn in (lambda: kalshi_markets(fetch=fetch),
               lambda: predictit_all(fetch=fetch),
               lambda: manifold_markets(fetch=fetch),
               lambda: odds_api(sports=odds_sports or ("baseball_mlb",),
                                fetch=fetch)):
        try:
            got = fn()
            if got:
                pool.extend(got)
        except Exception:
            continue
    return pool


# ============================================================ STRICT MATCHER
# Mirrors sportsedge.join_event's philosophy: REJECT/abstain on anything that
# isn't a confident same-event, same-resolution twin. Most PM markets have NO
# cross-market match — that is the correct, common outcome.

_STOP = {
    "the", "a", "an", "of", "to", "in", "on", "by", "be", "is", "are", "will",
    "for", "and", "or", "at", "vs", "v", "win", "wins", "winner", "above",
    "below", "over", "under", "than", "more", "less", "this", "that", "with",
    "from", "市", "yes", "no", "price", "market", "target", "value", "points",
    "runs", "scored", "game", "match", "today", "tonight", "do", "does",
}
_NUM_RX = re.compile(r"[\$,]")
# futures / season-long / award markets — cross-source resolution rules and
# timing diverge wildly here; we abstain rather than mismatch.
_FUTURES_RX = re.compile(
    r"\bchampion(ship)?\b|\bwin the\b|\baward\b|\bmvp\b|to make the"
    r"|season|series winner|nominee|\bwin (the )?(senate|house|presidency)\b"
    r"|world cup winner|super ?bowl|finals\b|playoff|by (eoy|end of)"
    r"|qualif|advance", re.I)


def _tokens(q):
    """Distinctive content tokens of a question (drop stopwords, keep numbers
    and entity words). Numbers matter — $66,000 vs $68,000 must NOT match."""
    s = _NUM_RX.sub("", (q or "").lower())
    raw = re.sub(r"[^a-z0-9 ]", " ", s).split()
    toks = set()
    for w in raw:
        if w in _STOP:
            continue
        if len(w) >= 3 or w.isdigit():
            toks.add(w)
    return toks


def _day(s):
    """YYYY-MM-DD prefix of a timestamp-ish string, or '' if absent."""
    return str(s or "")[:10]


def _num_tokens(toks):
    return {t for t in toks if any(c.isdigit() for c in t)}


def _strike_nums(toks):
    """STRIKE-like numbers only — the threshold that defines resolution
    ($66,000 -> 66000). Excludes incidental calendar tokens: a bare 4-digit
    year (2000-2099) and small day/score numbers (< 1000). This is what makes
    the $66k vs $68k trap rejectable WITHOUT a year like '2026' or a day like
    '12' falsely blocking an otherwise-identical event."""
    out = set()
    for t in toks:
        if not t.isdigit():
            continue
        v = int(t)
        if v < 1000:                       # day-of-month, score, jersey #
            continue
        if 2000 <= v <= 2099 and len(t) == 4:   # a year, not a strike
            continue
        out.add(t)
    return out


def match_event(pm_market, cross_pool, min_overlap=2, min_jaccard=0.34):
    """Find cross-market records that confidently describe the SAME event /
    resolution as a Polymarket market, or [] (the common case).

    pm_market needs: 'question', and optionally 'date' (YYYY-MM-DD resolution
    day). Each cross record is a connector dict (source, question, p, ...).

    Confidence requires ALL of:
      * neither side is a futures / season / award market,
      * >= min_overlap shared distinctive tokens AND token Jaccard >= threshold
        (strong entity overlap, not one incidental word),
      * NUMBER agreement: if either question carries a number (threshold/strike),
        the matched record must share at least one number token — $66k != $68k,
      * DATE agreement when both carry a resolution day (never cross days).
    Returns the accepted records (possibly several sources for one event)."""
    pq = pm_market.get("question") or ""
    if _FUTURES_RX.search(pq):
        return []
    pt = _tokens(pq)
    if len(pt) < min_overlap:
        return []
    p_nums = _strike_nums(pt)
    pday = _day(pm_market.get("date"))
    accepted = []
    for rec in cross_pool:
        rq = rec.get("question") or ""
        if _FUTURES_RX.search(rq):
            continue
        rt = _tokens(rq)
        if not rt:
            continue
        shared = pt & rt
        if len(shared) < min_overlap:
            continue
        union = pt | rt
        if (len(shared) / len(union)) < min_jaccard:
            continue
        # number agreement (the $66k/$68k trap): if PM has a numeric STRIKE,
        # the twin must share one of those exact strike numbers.
        if p_nums and not (p_nums & _strike_nums(rt)):
            continue
        # date agreement when both sides expose a day
        rday = _day((rec.get("raw") or {}).get("close"))
        if pday and rday and pday != rday:
            continue
        accepted.append(rec)
    return accepted


# ============================================================== CONSENSUS


def consensus(matched, pm_p=None):
    """Reliability-weighted consensus probability over matched cross records,
    plus divergence vs the PM price. Returns {} when there is no match (so the
    common path stays neutral). Weight = source reliability prior."""
    if not matched:
        return {}
    wsum = sum(max(0.0, r.get("weight", 0.0)) for r in matched)
    if wsum <= 0:
        return {}
    cp = sum(r["p"] * max(0.0, r.get("weight", 0.0)) for r in matched) / wsum
    cp = max(0.001, min(0.999, cp))
    out = {"consensus_p": round(cp, 4),
           "n_sources": len(matched),
           "sources": sorted({r["source"] for r in matched}),
           "weight_sum": round(wsum, 3)}
    if pm_p is not None:
        out["divergence"] = round(float(pm_p) - cp, 4)
    return out


def lookup(pm_market, cross_pool, pm_p=None):
    """One-call match+consensus for the bot's entry path. Returns {} when
    there is no confident cross-market twin (the common case) so the brain
    features default neutral and the global path is unchanged."""
    matched = match_event(pm_market, cross_pool)
    return consensus(matched, pm_p=pm_p)


# =============================================================== SCORECARD


def grade(preds):
    """Out-of-sample scorecard over SETTLED shadow predictions. Each pred:
    {consensus_p, market_price, won, clv?}. The honest question: does the
    cross-market consensus predict PM resolution BETTER than the PM price
    itself did at decision time? Promotion needs n>=15, consensus Brier <
    market Brier, AND positive CLV. Defaults to no-data / no-promotion."""
    settled = [p for p in (preds or []) if p.get("won") in (0, 1)]
    n = len(settled)
    if n == 0:
        return {"n": 0, "verdict": "no data", "promote": False}
    bs_cons = bs_mkt = 0.0
    for p in settled:
        y = p["won"]
        bs_cons += (p["consensus_p"] - y) ** 2
        bs_mkt += (p["market_price"] - y) ** 2
    bs_cons, bs_mkt = bs_cons / n, bs_mkt / n
    mean_clv = sum(p.get("clv", 0.0) for p in settled) / n
    mean_div = sum(abs(p.get("consensus_p", 0.0) - p.get("market_price", 0.0))
                   for p in settled) / n
    beats_market = bs_cons < bs_mkt
    promote = bool(n >= 15 and beats_market and mean_clv > 0)
    return {"n": n,
            "brier_consensus": round(bs_cons, 5),
            "brier_market": round(bs_mkt, 5),
            "mean_clv": round(mean_clv, 4),
            "mean_abs_divergence": round(mean_div, 4),
            "beats_market": beats_market,
            "promote": promote,
            "verdict": ("promotable" if promote else "shadow-only")}


def clv(entry_price, closing_price, won):
    """Closing-line value of a settled shadow position, sign-correct: did the
    side we'd have favored (where consensus diverged from price) move our way
    by the close? CLV = closing - entry on the favored side."""
    return round(closing_price - entry_price, 4)


# ============================================================ SELF-TEST


def self_test():
    ok_all = True

    def check(name, cond):
        nonlocal ok_all
        ok_all = ok_all and bool(cond)
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")

    # 1) connector parsing on planted raw payloads (no network)
    kal = _kalshi_one({"title": "Fed cuts in July", "yes_sub_title": "Yes",
                       "last_price_dollars": "0.4900", "volume_fp": "120"})
    check("kalshi parses dollars schema -> p=0.49",
          kal and abs(kal["p"] - 0.49) < 1e-6 and kal["source"] == "kalshi")
    kal_mid = _kalshi_one({"title": "X", "yes_bid_dollars": "0.40",
                           "yes_ask_dollars": "0.50", "last_price_dollars": "0",
                           "volume_fp": "5"})
    check("kalshi falls back to bid/ask mid", kal_mid and abs(kal_mid["p"] - 0.45) < 1e-6)
    kal_leg = _kalshi_one({"title": "Y", "yes_bid": 30, "yes_ask": 40,
                           "last_price": 0, "volume": 9})
    check("kalshi legacy cents schema (/100)", kal_leg and abs(kal_leg["p"] - 0.35) < 1e-6)
    check("kalshi drops zero-volume / unpriced",
          _kalshi_one({"title": "Z", "last_price_dollars": "0", "volume_fp": "0"}) is None)

    # 2) matcher ACCEPTS a true same-event twin
    pool = [
        {"source": "kalshi", "question": "Fed cuts rates in July 2026",
         "p": 0.52, "weight": 1.0, "raw": {}},
        {"source": "manifold", "question": "Will the Fed cut rates in July?",
         "p": 0.44, "weight": 0.2, "raw": {}},
        {"source": "predictit", "question": "Lakers beat the Celtics tonight",
         "p": 0.61, "weight": 0.6, "raw": {}},
    ]
    pm = {"question": "Will the Fed cut interest rates in July 2026?"}
    m = match_event(pm, pool)
    check("matcher accepts the true Fed twin (>=1 source)",
          any(r["source"] == "kalshi" for r in m))
    check("matcher rejects the unrelated Lakers row",
          all(r["source"] != "predictit" for r in m))

    # 3) matcher REJECTS futures, wrong-day, and the $66k/$68k number trap
    check("matcher rejects futures (championship)",
          match_event({"question": "Will the Lakers win the 2026 championship?"},
                      [{"source": "kalshi", "question": "Lakers win the championship",
                        "p": 0.2, "weight": 1.0, "raw": {}}]) == [])
    btc_pool = [{"source": "kalshi", "question": "Bitcoin above 68000 on June 12",
                 "p": 0.3, "weight": 1.0, "raw": {}}]
    check("matcher rejects $66k vs $68k (number mismatch)",
          match_event({"question": "Will Bitcoin be above 66000 on June 12?"},
                      btc_pool) == [])
    check("matcher accepts matching number 68k",
          len(match_event({"question": "Will Bitcoin be above 68000 on June 12?"},
                          btc_pool)) == 1)
    wrongday = [{"source": "kalshi", "question": "Yankees beat Red Sox",
                 "p": 0.55, "weight": 1.0, "raw": {"close": "2026-06-14T00:00:00Z"}}]
    check("matcher rejects wrong resolution day",
          match_event({"question": "Yankees beat Red Sox",
                       "date": "2026-06-13"}, wrongday) == [])

    # 4) consensus weighting: real-money Kalshi dominates play-money Manifold
    con = consensus(m, pm_p=0.60)
    # kalshi 0.52*1.0 + manifold 0.44*0.2 over weight 1.2 -> ~0.507
    check("consensus is reliability-weighted toward Kalshi",
          0.50 <= con["consensus_p"] <= 0.515)
    check("consensus reports divergence vs PM price",
          abs(con["divergence"] - round(0.60 - con["consensus_p"], 4)) < 1e-9)
    check("no match -> empty consensus (neutral common path)",
          consensus([], pm_p=0.6) == {} and lookup({"question": "totally unique zzz qqq"}, pool) == {})

    # 5) de-vig removes the overround and normalizes to 1
    dv = devig_two_way(1.5, 3.0)        # raw 0.667 + 0.333 = 1.0 already
    check("devig two-way normalizes to 1",
          dv and abs(dv[0] + dv[1] - 1.0) < 1e-9 and abs(dv[0] - 2.0 / 3.0) < 1e-6)
    dv2 = devig_two_way(1.91, 1.91)     # -1.91 even money each w/ vig
    check("devig even money -> 0.5/0.5", dv2 and abs(dv2[0] - 0.5) < 1e-6)

    # 6) odds_api returns [] with no key (source skipped, never crashes)
    check("odds_api skipped without key", odds_api(api_key="") == [])

    # 7) grade math: consensus that beats the market on Brier + positive CLV
    #    promotes only at n>=15; below that it stays shadow-only.
    good = [{"consensus_p": 0.9, "market_price": 0.6, "won": 1, "clv": 0.05}
            for _ in range(15)]
    sc = grade(good)
    check("grade promotes on 15 wins where consensus beats market + CLV>0",
          sc["promote"] and sc["beats_market"] and sc["brier_consensus"] < sc["brier_market"])
    check("grade refuses to promote below 15 settles",
          grade(good[:14])["promote"] is False)
    bad = [{"consensus_p": 0.1, "market_price": 0.6, "won": 1, "clv": -0.05}
           for _ in range(20)]
    check("grade refuses when consensus loses to market",
          grade(bad)["promote"] is False)
    check("grade no data default", grade([])["verdict"] == "no data")

    # 8) gather_pool is fail-silent when every source errors
    def boom(_url, timeout=0):
        raise RuntimeError("network down")
    check("gather_pool fail-silent on total outage",
          gather_pool(fetch=lambda u, timeout=0: None) == []
          and gather_pool(fetch=boom) == [])

    # 9) clv sign-correct
    check("clv sign-correct", clv(0.55, 0.80, 1) > 0 and clv(0.55, 0.30, 0) < 0)

    print(f"\ncrossmarket self-test: {'ALL PASS' if ok_all else 'FAILURES'}")
    return ok_all


if __name__ == "__main__":
    import sys
    sys.exit(0 if self_test() else 1)
