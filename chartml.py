"""chartml.py — the chart-analysis & pattern-mining ML category.

Two hand-coded systems become learners here:

1. THE CHARTIST learned its shapes from my intuition (spike_fade,
   breakout, mean_dev). This module replaces intuition with data: every
   recorded price path in tick memory contains thousands of self-labeled
   training events — "a 3c move happened HERE; did it revert or continue
   within 30 minutes?" No human labels needed, the future of each window
   is already on disk. The move model trains on those events walk-forward
   and gates the day-trade fade: only fade what history says actually
   reverts.

2. THE PATTERN MINER vetoes trait combos by dollar thresholds alone — a
   combo can earn a veto from bad luck. binom_p gives it an exact
   significance test: a veto now also needs the losses to be statistically
   surprising, not just expensive.

Same contracts as ml.py: pure Python, deterministic, self-proving on
planted problems before anyone trusts it with money.
"""

import math
import random

import ml

# ------------------------------------------------- feature extraction


def chart_x(pts):
    """A price path -> learnable features. pts: [(ts, price)] ascending,
    ideally ~6h. Returns None if the path is too thin to describe."""
    if len(pts) < 12:
        return None
    ts = [p[0] for p in pts]
    px = [p[1] for p in pts]
    span = ts[-1] - ts[0]
    if span < 900:
        return None
    n = len(px)
    rets = [px[i] - px[i - 1] for i in range(1, n)]
    mu = sum(rets) / len(rets)
    sd = (sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5

    def ret_since(seconds):
        cutoff = ts[-1] - seconds
        for i in range(n - 1, -1, -1):
            if ts[i] <= cutoff:
                return px[-1] - px[i]
        return px[-1] - px[0]

    lo, hi = min(px), max(px)
    # trend strength: r^2 of price vs time
    tmu = sum(ts) / n
    pmu = sum(px) / n
    cov = sum((ts[i] - tmu) * (px[i] - pmu) for i in range(n))
    vt = sum((t - tmu) ** 2 for t in ts)
    vp = sum((p - pmu) ** 2 for p in px)
    r2 = (cov * cov / (vt * vp)) if vt > 0 and vp > 0 else 0.0
    slope = (cov / vt) * 3600 if vt > 0 else 0.0          # price per hour
    # max drawdown / drawup within the window
    peak, dd, trough, du = px[0], 0.0, px[0], 0.0
    for p in px:
        peak, trough = max(peak, p), min(trough, p)
        dd, du = max(dd, peak - p), max(du, p - trough)
    # lag-1 autocorrelation of returns: momentum (+) vs mean-reversion (-)
    ac1 = 0.0
    if sd > 1e-9 and len(rets) > 3:
        ac1 = (sum((rets[i] - mu) * (rets[i - 1] - mu)
                   for i in range(1, len(rets)))
               / ((len(rets) - 1) * sd * sd))
    half = rets[len(rets) // 2:]
    sd_recent = ((sum((r - mu) ** 2 for r in half) / len(half)) ** 0.5
                 if half else sd)
    return {
        "price": px[-1] - 0.5,
        "ret_30m": max(-0.3, min(0.3, ret_since(1800))) * 5,
        "ret_2h": max(-0.4, min(0.4, ret_since(7200))) * 4,
        "ret_6h": max(-0.5, min(0.5, px[-1] - px[0])) * 3,
        "vol": min(sd * 200, 3.0),
        "vol_ratio": min(sd_recent / sd, 3.0) if sd > 1e-9 else 1.0,
        "trend_r2": r2,
        "slope": max(-1.5, min(1.5, slope * 10)),
        "maxdd": min(dd * 10, 3.0),
        "maxdu": min(du * 10, 3.0),
        "range_pos": ((px[-1] - lo) / (hi - lo) - 0.5) * 2 if hi > lo else 0.0,
        "ac1": max(-1.0, min(1.0, ac1)),
        "spike_z": max(-4.0, min(4.0, rets[-1] / sd)) if sd > 1e-9 else 0.0,
    }


# ------------------------------------------- self-labeled move events


def build_move_events(series, move=0.03, window_s=900, horizon_s=1800,
                      revert=0.01, lookback_s=21600, max_events=12000):
    """Mine every recorded price path for fade-or-follow training events.
    An event: price moved >= `move` within `window_s`. Label: 1 if it
    reverted >= `revert` within `horizon_s`, else 0. Features describe the
    `lookback_s` of path BEFORE the move completed — strictly no future
    information leaks into x. Returns events sorted by time."""
    events = []
    for key, pts in series.items():
        # Resample to the SAME 30s bars the inference path scores on
        # (move_predict -> chart_x(_bars(pts))). Do it once, up front, so
        # event detection, the horizon_s cooldown, the index-based j-360
        # lookback AND the chart_x features all run on training cadence ==
        # serve cadence. Aligning only the feature slice would leave
        # detection/cooldown/lookback on raw ticks and create a new skew.
        pts = _bars(pts)
        n = len(pts)
        if n < 30:
            continue
        last_ev = 0.0
        for i in range(12, n):
            t_i, p_i = pts[i]
            if t_i - last_ev < horizon_s:      # one event per cooldown
                continue
            j = i - 1
            while j >= 0 and t_i - pts[j][0] <= window_s:
                j -= 1
            j += 1
            if j >= i:
                continue
            delta = p_i - pts[j][1]
            if abs(delta) < move or not 0.05 <= p_i <= 0.95:
                continue
            x = chart_x([p for p in pts[max(0, j - 360):i + 1]
                         if t_i - p[0] <= lookback_s])
            if x is None:
                continue
            x["move"] = max(-1.5, min(1.5, delta * 20))
            fut = [p for p in pts[i + 1:] if p[0] - t_i <= horizon_s]
            if len(fut) < 3:
                continue
            sgn = 1.0 if delta > 0 else -1.0
            best_rev = max(sgn * (p_i - fp) for _, fp in fut)
            events.append((t_i, x, 1.0 if best_rev >= revert else 0.0))
            last_ev = t_i
    events.sort(key=lambda e: e[0])
    return events[-max_events:]


def train_move_model(events):
    """Walk-forward championship on real events: first 70% trains, last
    30% judges (chronological — the model never sees its own future).
    Returns a state dict; 'skill' > 0 means it beats the base rate
    out-of-sample, which is the bar for gate power."""
    if len(events) < 200:
        return {"model": None, "n_events": len(events),
                "note": "need 200+ events"}
    cut = int(len(events) * 0.7)
    train = [(x, y) for _, x, y in events[:cut]]
    hold = [(x, y) for _, x, y in events[cut:]]
    base = max(0.02, min(0.98, sum(y for _, y in train) / len(train)))
    results = {}
    for name in ("gbm", "xgb", "forest"):
        try:
            m = ml.ZOO[name](train)
            ll_m = ll_b = 0.0
            for x, y in hold:
                p = max(0.02, min(0.98, ml.predict(m, x)))
                ll_m += -(y * math.log(p) + (1 - y) * math.log(1 - p))
                ll_b += -(y * math.log(base) + (1 - y) * math.log(1 - base))
            results[name] = round((ll_b - ll_m) / len(hold), 5)
        except Exception:
            pass
    if not results:
        return {"model": None, "n_events": len(events), "note": "fits failed"}
    champ = max(results, key=results.get)
    out = {"n_events": len(events), "base_rate": round(base, 4),
           "zoo": results, "champion": champ,
           "skill": results[champ], "n_holdout": len(hold)}
    # refit champion on everything only AFTER the honest verdict is locked
    out["model"] = ml.ZOO[champ]([(x, y) for _, x, y in events])
    cal_hold = [(ml.predict(out["model"], x), y) for x, y in hold]
    out["cal"] = ml.fit_platt(cal_hold) if len(cal_hold) >= 30 else None
    return out


def _bars(pts, dt=30.0):
    """Resample to fixed 30s last-value bars. The move model TRAINS on the
    recorder's 30s series; scoring it on 1-15s token ticks shifts every
    per-sample feature (train/serve skew — the advertised OOS skill does
    not transfer unless inference sees training cadence)."""
    out, last_b = [], None
    for t, p in pts:
        b = int(t // dt) * dt
        if last_b == b:
            out[-1] = (float(b), p)
        else:
            out.append((float(b), p))
            last_b = b
    return out


def move_predict(state, pts, delta):
    """P(this move reverts) from the trained model; None = no opinion."""
    if not state or not state.get("model"):
        return None
    x = chart_x(_bars(pts))
    if x is None:
        return None
    x["move"] = max(-1.5, min(1.5, delta * 20))
    try:
        p = ml.predict(state["model"], x)
        return ml.apply_cal(state["cal"], p) if state.get("cal") else p
    except Exception:
        return None


# --------------------------------------------- miner significance test


def binom_p(wins, n, p0=0.5):
    """Exact one-sided binomial tail: P(<= wins | n, p0). Small enough to
    be honest at miner sample sizes — a veto must be SURPRISING under
    'this pattern is actually fine', not merely expensive."""
    if n == 0:
        return 1.0
    total = 0.0
    for k in range(wins + 1):
        total += math.comb(n, k) * (p0 ** k) * ((1 - p0) ** (n - k))
    return min(1.0, total)


# --------------------------------------------------------- self-test


def _synth_series(kind, rng, n=900):
    """Planted regimes: 'ou' mean-reverts after shocks, 'mom' trends
    through them. The move model must learn to tell them apart."""
    px, p = [], 0.5
    drift = 0.0
    for i in range(n):
        if rng.random() < 0.02:                      # shock
            p += rng.choice([-1, 1]) * rng.uniform(0.03, 0.06)
        if kind == "ou":
            p += (0.5 - p) * 0.05 + rng.gauss(0, 0.002)
        else:
            drift = drift * 0.97 + rng.gauss(0, 0.004) * 0.4
            p += drift + rng.gauss(0, 0.001)
        p = max(0.03, min(0.97, p))
        px.append((i * 60.0, p))
    return px


def self_test():
    rng = random.Random(2)
    series = {f"ou{i}": _synth_series("ou", rng) for i in range(30)}
    series |= {f"mo{i}": _synth_series("mom", rng) for i in range(30)}
    ev = build_move_events(series, move=0.025, horizon_s=2400)
    st = train_move_model(ev)
    ok1 = st.get("skill") is not None and st["skill"] > 0.01
    print(f"  {'PASS' if ok1 else 'FAIL'}  move model separates planted "
          f"regimes OOS (skill={st.get('skill')}, n={st.get('n_events')})")
    x = chart_x([(i * 60.0, 0.5 + 0.001 * i) for i in range(60)])
    ok2 = x is not None and x["trend_r2"] > 0.95 and x["slope"] > 0
    print(f"  {'PASS' if ok2 else 'FAIL'}  chart_x reads a pure trend "
          f"(r2={x['trend_r2']:.2f})")
    ok3 = (binom_p(1, 10, 0.5) < 0.05 < binom_p(4, 10, 0.5)
           and binom_p(0, 3, 0.5) > 0.05)
    print(f"  {'PASS' if ok3 else 'FAIL'}  binomial test: 1/10 surprising, "
          f"4/10 and 0/3 not")
    # no-future-leak property: events from a series cut at time T must be
    # identical to events from the full series up to T - horizon
    full = {"s": series["ou0"]}
    cut_t = series["ou0"][600][0]
    cut = {"s": [p for p in series["ou0"] if p[0] <= cut_t]}
    e_full = [e[0] for e in build_move_events(full) if e[0] <= cut_t - 2400]
    e_cut = [e[0] for e in build_move_events(cut) if e[0] <= cut_t - 2400]
    ok4 = e_full == e_cut
    print(f"  {'PASS' if ok4 else 'FAIL'}  event builder leaks no future "
          f"({len(e_full)} events stable under truncation)")
    good = ok1 and ok2 and ok3 and ok4
    print(f"\nchartml self-test: {'ALL PASS' if good else 'FAILURES'}")
    return good


if __name__ == "__main__":
    import sys
    sys.exit(0 if self_test() else 1)
