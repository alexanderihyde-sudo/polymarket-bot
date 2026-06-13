"""ml.py — the bot's machine-learning library. Pure Python, zero
dependencies, deterministic. Every learner here exposes the same tiny
interface so the brain's cross-validation harness can race them fairly:

    model = fit_xxx(data)          # data: list of (feature_dict, label)
    p     = predict(model, x)      # x: feature_dict -> probability 0..1

Model classes, in increasing capacity:
  - decision stumps via AdaBoost      (in bot.py, historical)
  - GBM: gradient-boosted depth-2 trees (this file)
  - random forest: bagged depth-2 trees with feature subsampling
  - MLP: one-hidden-layer neural network, manual backprop

Why depth-2 trees: with ~100-1000 training rows, deeper trees memorize.
Depth-2 captures one interaction per tree (the pattern-miner's "pairs"
in continuous form) while staying honest at our sample sizes.

Why pure Python: the bot must run forever on a laptop with no pip
installs, no wheels breaking on macOS updates, and no numpy version
drama at 3am. At n<10k rows, vectorization buys nothing that matters.
"""

import math
import random

# --------------------------------------------------------------- utils


def _keys(data):
    """Stable, sorted feature-name list from the first row."""
    return sorted(data[0][0].keys())


def _sigmoid(z):
    return 1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, z))))


def _logloss(p, y):
    p = max(1e-6, min(1 - 1e-6, p))
    return -(y * math.log(p) + (1 - y) * math.log(1 - p))


def predict(model, x):
    """Single dispatch for every model class in this file."""
    kind = model.get("kind")
    if kind in ("gbm", "xgb"):       # both are additive tree ensembles
        return _predict_gbm(model, x)
    if kind == "forest":
        return _predict_forest(model, x)
    if kind == "mlp":
        return _predict_mlp(model, x)
    raise ValueError(f"unknown model kind: {kind}")


# ----------------------------------------------------- depth-2 trees

# A depth-2 regression tree is stored as a flat dict:
#   {"f1": feat, "t1": thresh,                      # root split
#    "lf": feat, "lt": thresh, "ll": v, "lr": v,    # left child split+leaves
#    "rf": feat, "rt": thresh, "rl": v, "rr": v}    # right child split+leaves
# Leaves hold real values (residual fits for GBM, class fractions for RF).


def _best_split(rows, grads, feats, rng, k_feats=None):
    """Find the single split that minimizes squared error of the gradient.
    rows: list of feature dicts; grads: list of targets to fit.
    Returns (feat, thresh, left_idx, right_idx) or None."""
    n = len(rows)
    if n < 8:
        return None
    cand = feats if k_feats is None else rng.sample(feats,
                                                    min(k_feats, len(feats)))
    total = sum(grads)
    best, best_score = None, None
    for f in cand:
        vals = sorted({r[f] for r in rows})
        if len(vals) < 2:
            continue
        step = max(1, len(vals) // 8)
        for t in vals[step::step][:8]:
            left = [i for i in range(n) if rows[i][f] <= t]
            if len(left) < 4 or len(left) > n - 4:
                continue
            sl = sum(grads[i] for i in left)
            nl = len(left)
            sr, nr = total - sl, n - nl
            # maximizing sum-of-(mean^2 * count) == minimizing SSE
            score = sl * sl / nl + sr * sr / nr
            if best_score is None or score > best_score:
                best_score = score
                best = (f, t, left)
    if best is None:
        return None
    f, t, left = best
    right = [i for i in range(n) if rows[i][f] > t]
    return f, t, left, right


def _leaf(grads, idx, shrink=1.0):
    if not idx:
        return 0.0
    return shrink * sum(grads[i] for i in idx) / len(idx)


def _fit_tree2(rows, grads, feats, rng, k_feats=None, shrink=1.0):
    """One depth-2 tree fit to `grads` (gradient residuals or labels)."""
    root = _best_split(rows, grads, feats, rng, k_feats)
    if root is None:
        return {"const": _leaf(grads, list(range(len(rows))), shrink)}
    f1, t1, li, ri = root
    tree = {"f1": f1, "t1": t1}
    for side, idx, pf, pt, pl, pr in (("l", li, "lf", "lt", "ll", "lr"),
                                      ("r", ri, "rf", "rt", "rl", "rr")):
        sub_rows = [rows[i] for i in idx]
        sub_g = [grads[i] for i in idx]
        sp = _best_split(sub_rows, sub_g, feats, rng, k_feats)
        if sp is None:
            tree[pf] = None
            tree[pl] = _leaf(grads, idx, shrink)
            tree[pr] = tree[pl]
            tree[pt] = 0.0
        else:
            f2, t2, l2, r2 = sp
            tree[pf], tree[pt] = f2, t2
            tree[pl] = _leaf(sub_g, l2, shrink)
            tree[pr] = _leaf(sub_g, r2, shrink)
    return tree


def _eval_tree2(tree, x):
    if "const" in tree:
        return tree["const"]
    if x.get(tree["f1"], 0.0) <= tree["t1"]:
        f, t, lo, hi = tree["lf"], tree["lt"], tree["ll"], tree["lr"]
    else:
        f, t, lo, hi = tree["rf"], tree["rt"], tree["rl"], tree["rr"]
    if f is None:
        return lo
    return lo if x.get(f, 0.0) <= t else hi


# --------------------------------------------------------------- GBM


def fit_gbm(data, rounds=80, lr=0.15, seed=7, subsample=0.8,
            early_stop=True):
    """Stochastic gradient boosting with logloss and early stopping:
    each tree fits the residual (y - p) of the ensemble so far, on a
    random 80% subsample (decorrelates trees, fights overfit), and
    boosting HALTS when a held-out 15% slice stops improving — the model
    decides its own size instead of trusting a rounds knob."""
    rng = random.Random(seed)
    n = len(data)
    val_n = max(5, int(n * 0.15)) if early_stop and n >= 40 else 0
    idx = list(range(n))
    rng.shuffle(idx)
    val_i = set(idx[:val_n])
    tr_i = [i for i in range(n) if i not in val_i]
    rows = [data[i][0] for i in tr_i]
    ys = [data[i][1] for i in tr_i]
    feats = _keys(data)
    base = max(1e-3, min(1 - 1e-3, sum(ys) / len(ys)))
    f0 = math.log(base / (1 - base))
    scores = [f0] * len(rows)
    val_scores = {i: f0 for i in val_i}
    trees, best_ll, since_best, kept = [], None, 0, 0
    for _ in range(rounds):
        sub = [i for i in range(len(rows)) if rng.random() < subsample]             or list(range(len(rows)))
        grads_all = [ys[i] - _sigmoid(scores[i]) for i in range(len(rows))]
        tree = _fit_tree2([rows[i] for i in sub],
                          [grads_all[i] for i in sub], feats, rng)
        trees.append(tree)
        for i in range(len(rows)):
            scores[i] += lr * _eval_tree2(tree, rows[i])
        if val_i:
            for i in val_i:
                val_scores[i] += lr * _eval_tree2(tree, data[i][0])
            ll = sum(_logloss(_sigmoid(val_scores[i]), data[i][1])
                     for i in val_i) / len(val_i)
            if best_ll is None or ll < best_ll - 1e-5:
                best_ll, since_best, kept = ll, 0, len(trees)
            else:
                since_best += 1
                if since_best >= 5:
                    trees = trees[:kept]      # rewind to the best point
                    break
    return {"kind": "gbm", "f0": round(f0, 5), "lr": lr,
            "trees": trees, "feats": feats,
            "n_trees": len(trees),
            "val_logloss": round(best_ll, 5) if best_ll else None}


def _predict_gbm(model, x):
    s = model["f0"]
    for tree in model["trees"]:
        s += model["lr"] * _eval_tree2(tree, x)
    return _sigmoid(s)


# ------------------------------------------- Newton boosting (XGBoost)


def _best_split_xgb(rows, gs, hs, feats, rng, lam, gamma, k_feats=None):
    """XGBoost's split rule (github.com/dmlc/xgboost): score candidates by
    the regularized gain  ½[G_L²/(H_L+λ) + G_R²/(H_R+λ) − G²/(H+λ)] − γ
    and refuse to split at all when no candidate clears γ — pruning is
    built into the objective instead of bolted on after."""
    n = len(rows)
    if n < 8:
        return None
    cand = feats if k_feats is None else rng.sample(feats,
                                                    min(k_feats, len(feats)))
    G, H = sum(gs), sum(hs)
    parent = G * G / (H + lam)
    best, best_gain = None, 0.0
    for f in cand:
        vals = sorted({r[f] for r in rows})
        if len(vals) < 2:
            continue
        step = max(1, len(vals) // 8)
        for t in vals[step::step][:8]:
            li = [i for i in range(n) if rows[i][f] <= t]
            if len(li) < 4 or len(li) > n - 4:
                continue
            GL = sum(gs[i] for i in li)
            HL = sum(hs[i] for i in li)
            GR, HR = G - GL, H - HL
            gain = 0.5 * (GL * GL / (HL + lam)
                          + GR * GR / (HR + lam) - parent) - gamma
            if gain > best_gain:
                best_gain, best = gain, (f, t, li)
    if best is None:
        return None
    f, t, li = best
    return f, t, li, [i for i in range(n) if rows[i][f] > t]


def _xleaf(gs, hs, idx, lam):
    """Newton leaf weight w* = −G/(H+λ): big curvature or thin data both
    shrink the step automatically."""
    if not idx:
        return 0.0
    return -sum(gs[i] for i in idx) / (sum(hs[i] for i in idx) + lam)


def _fit_tree2_xgb(rows, gs, hs, feats, rng, lam, gamma, k_feats=None):
    root = _best_split_xgb(rows, gs, hs, feats, rng, lam, gamma, k_feats)
    if root is None:
        return {"const": _xleaf(gs, hs, list(range(len(rows))), lam)}
    f1, t1, li, ri = root
    tree = {"f1": f1, "t1": t1}
    for idx, pf, pt, pl, pr in ((li, "lf", "lt", "ll", "lr"),
                                (ri, "rf", "rt", "rl", "rr")):
        sub_r = [rows[i] for i in idx]
        sub_g = [gs[i] for i in idx]
        sub_h = [hs[i] for i in idx]
        sp = _best_split_xgb(sub_r, sub_g, sub_h, feats, rng,
                             lam, gamma, k_feats)
        if sp is None:
            tree[pf], tree[pt] = None, 0.0
            tree[pl] = tree[pr] = _xleaf(gs, hs, idx, lam)
        else:
            f2, t2, l2, r2 = sp
            tree[pf], tree[pt] = f2, t2
            tree[pl] = _xleaf(sub_g, sub_h, l2, lam)
            tree[pr] = _xleaf(sub_g, sub_h, r2, lam)
    return tree


def fit_xgb(data, rounds=80, lr=0.15, lam=1.0, gamma=0.0, seed=7,
            subsample=0.8, colsample=0.8, early_stop=True):
    """Newton boosting, the XGBoost way: each tree is built from the
    gradient AND the curvature of the loss. Where plain GBM fits residuals
    with equal trust everywhere, Newton leaves (−G/(H+λ)) step small where
    the model is already confident, λ shrinks leaves grown on thin data,
    γ-gain pruning refuses splits that don't pay for themselves, and
    column subsampling decorrelates trees beyond what row subsampling
    alone buys. Same early-stopping contract as fit_gbm."""
    rng = random.Random(seed)
    n = len(data)
    val_n = max(5, int(n * 0.15)) if early_stop and n >= 40 else 0
    idx = list(range(n))
    rng.shuffle(idx)
    val_i = set(idx[:val_n])
    tr_i = [i for i in range(n) if i not in val_i]
    rows = [data[i][0] for i in tr_i]
    ys = [data[i][1] for i in tr_i]
    feats = _keys(data)
    k_feats = max(2, int(len(feats) * colsample))
    base = max(1e-3, min(1 - 1e-3, sum(ys) / len(ys)))
    f0 = math.log(base / (1 - base))
    scores = [f0] * len(rows)
    val_scores = {i: f0 for i in val_i}
    trees, best_ll, since_best, kept = [], None, 0, 0
    for _ in range(rounds):
        ps = [_sigmoid(s) for s in scores]
        gs_all = [ps[i] - ys[i] for i in range(len(rows))]
        hs_all = [max(1e-6, ps[i] * (1 - ps[i])) for i in range(len(rows))]
        sub = ([i for i in range(len(rows)) if rng.random() < subsample]
               or list(range(len(rows))))
        tree = _fit_tree2_xgb([rows[i] for i in sub],
                              [gs_all[i] for i in sub],
                              [hs_all[i] for i in sub],
                              feats, rng, lam, gamma, k_feats)
        trees.append(tree)
        for i in range(len(rows)):
            scores[i] += lr * _eval_tree2(tree, rows[i])
        if val_i:
            for i in val_i:
                val_scores[i] += lr * _eval_tree2(tree, data[i][0])
            ll = sum(_logloss(_sigmoid(val_scores[i]), data[i][1])
                     for i in val_i) / len(val_i)
            if best_ll is None or ll < best_ll - 1e-5:
                best_ll, since_best, kept = ll, 0, len(trees)
            else:
                since_best += 1
                if since_best >= 5:
                    trees = trees[:kept]      # rewind to the best point
                    break
    return {"kind": "xgb", "f0": round(f0, 5), "lr": lr,
            "trees": trees, "feats": feats, "n_trees": len(trees),
            "val_logloss": round(best_ll, 5) if best_ll else None}


# ------------------------------------------------------ random forest


def fit_forest(data, n_trees=40, seed=11):
    """Bagged depth-2 trees with feature subsampling (sqrt(k) features per
    split). Variance reduction by committee: each tree sees a bootstrap
    sample and a random slice of the features, so their errors decorrelate
    and the average is steadier than any member — the right tool when the
    signal is weak and the data is noisy, which is exactly our regime."""
    rng = random.Random(seed)
    feats = _keys(data)
    k = max(2, int(math.sqrt(len(feats))))
    n = len(data)
    trees, oob_votes = [], {i: [] for i in range(n)}
    for _ in range(n_trees):
        idx = [rng.randrange(n) for _ in range(n)]
        in_bag = set(idx)
        rows = [data[i][0] for i in idx]
        ys = [data[i][1] for i in idx]
        tree = _fit_tree2(rows, ys, feats, rng, k_feats=k)
        trees.append(tree)
        for i in range(n):                    # out-of-bag: free validation —
            if i not in in_bag:               # each row scored only by trees
                oob_votes[i].append(_eval_tree2(tree, data[i][0]))  # that
    hits = tot = 0                            # never saw it
    for i, votes in oob_votes.items():
        if votes:
            tot += 1
            p = sum(votes) / len(votes)
            hits += (p >= 0.5) == (data[i][1] >= 0.5)
    return {"kind": "forest", "trees": trees, "feats": feats,
            "oob_accuracy": round(hits / tot, 4) if tot else None}


def _predict_forest(model, x):
    votes = [_eval_tree2(t, x) for t in model["trees"]]
    return max(0.0, min(1.0, sum(votes) / len(votes)))


# ------------------------------------------------------ neural network


def fit_mlp(data, hidden=12, epochs=700, lr=0.5, l2=5e-4, seed=13):
    """One-hidden-layer neural network, tanh activations, manual backprop,
    full-batch gradient descent. At our sample sizes a small MLP is the
    ceiling of justifiable capacity — it can learn smooth nonlinear
    surfaces the trees staircase over, while L2 and the tiny hidden layer
    keep it from memorizing 200 rows."""
    rng = random.Random(seed)
    feats = _keys(data)
    nf = len(feats)
    # standardize inputs: tanh saturates if features arrive on wild scales
    mu = {f: 0.0 for f in feats}
    sd = {f: 0.0 for f in feats}
    for f in feats:
        col = [x[f] for x, _ in data]
        mu[f] = sum(col) / len(col)
        sd[f] = (sum((v - mu[f]) ** 2 for v in col) / len(col)) ** 0.5 or 1.0
    X = [[(x[f] - mu[f]) / sd[f] for f in feats] for x, _ in data]
    Y = [y for _, y in data]
    lim = 1.0 / math.sqrt(nf)
    W1 = [[rng.uniform(-lim, lim) for _ in range(nf)] for _ in range(hidden)]
    b1 = [0.0] * hidden
    W2 = [rng.uniform(-lim, lim) for _ in range(hidden)]
    b2 = 0.0
    n = len(X)
    for _ in range(epochs):
        gW1 = [[0.0] * nf for _ in range(hidden)]
        gb1 = [0.0] * hidden
        gW2 = [0.0] * hidden
        gb2 = 0.0
        for xi, y in zip(X, Y):
            h = [math.tanh(sum(W1[j][k] * xi[k] for k in range(nf)) + b1[j])
                 for j in range(hidden)]
            p = _sigmoid(sum(W2[j] * h[j] for j in range(hidden)) + b2)
            d2 = p - y                       # dL/dz2 for logloss+sigmoid
            gb2 += d2
            for j in range(hidden):
                gW2[j] += d2 * h[j]
                d1 = d2 * W2[j] * (1 - h[j] * h[j])
                gb1[j] += d1
                for k in range(nf):
                    gW1[j][k] += d1 * xi[k]
        for j in range(hidden):
            b1[j] -= lr * gb1[j] / n
            W2[j] -= lr * (gW2[j] / n + l2 * W2[j])
            for k in range(nf):
                W1[j][k] -= lr * (gW1[j][k] / n + l2 * W1[j][k])
        b2 -= lr * gb2 / n
    return {"kind": "mlp", "feats": feats,
            "mu": {f: round(mu[f], 6) for f in feats},
            "sd": {f: round(sd[f], 6) for f in feats},
            "W1": [[round(v, 5) for v in row] for row in W1],
            "b1": [round(v, 5) for v in b1],
            "W2": [round(v, 5) for v in W2], "b2": round(b2, 5)}


def _predict_mlp(model, x):
    feats = model["feats"]
    xi = [(x.get(f, 0.0) - model["mu"][f]) / model["sd"][f] for f in feats]
    h = [math.tanh(sum(model["W1"][j][k] * xi[k] for k in range(len(feats)))
                   + model["b1"][j]) for j in range(len(model["W1"]))]
    return _sigmoid(sum(model["W2"][j] * h[j]
                        for j in range(len(h))) + model["b2"])


# -------------------------------------------------------- calibration


def fit_platt(pred_label_pairs):
    """Platt scaling: a tiny logistic fit p' = sigmoid(a*logit(p)+b) that
    repairs systematic over/under-confidence without touching ranking."""
    a, b = 1.0, 0.0
    pairs = [(max(1e-5, min(1 - 1e-5, p)), y) for p, y in pred_label_pairs]
    for _ in range(150):
        ga = gb = 0.0
        for p, y in pairs:
            z = math.log(p / (1 - p))
            q = _sigmoid(a * z + b)
            ga += (q - y) * z
            gb += (q - y)
        a -= 0.1 * ga / len(pairs)
        b -= 0.1 * gb / len(pairs)
    return {"a": round(a, 4), "b": round(b, 4)}


def apply_platt(cal, p):
    p = max(1e-5, min(1 - 1e-5, p))
    z = math.log(p / (1 - p))
    return _sigmoid(cal["a"] * z + cal["b"])


def fit_isotonic(pred_label_pairs):
    """Isotonic calibration via pool-adjacent-violators, the scikit-learn
    way (sklearn/_isotonic.pyx): aggregate ties, sort by prediction, then
    merge every adjacent block that violates monotonicity into its
    weighted mean. Nonparametric — it repairs ANY monotone miscalibration
    shape, where Platt's two parameters can only fix a tilt; the price is
    that it needs more data to be trustworthy."""
    agg = {}
    for p, y in pred_label_pairs:
        w, wy = agg.get(p, (0.0, 0.0))
        agg[p] = (w + 1.0, wy + float(y))
    blocks = [[w, wy, p, p] for p, (w, wy) in sorted(agg.items())]
    out = []
    for b in blocks:
        out.append(b)
        # pool while the previous block's mean exceeds this one's
        while (len(out) > 1
               and out[-2][1] * out[-1][0] > out[-1][1] * out[-2][0]):
            w, wy, _, hi = out.pop()
            out[-1][0] += w
            out[-1][1] += wy
            out[-1][3] = hi
    return {"x": [round((b[2] + b[3]) / 2, 5) for b in out],
            "y": [round(b[1] / b[0], 5) for b in out]}


def apply_isotonic(cal, p):
    """Step function with linear interpolation between block centers."""
    xs, ys = cal["x"], cal["y"]
    if not xs:
        return p
    if p <= xs[0]:
        return ys[0]
    if p >= xs[-1]:
        return ys[-1]
    for i in range(1, len(xs)):
        if p <= xs[i]:
            f = (p - xs[i - 1]) / ((xs[i] - xs[i - 1]) or 1.0)
            return ys[i - 1] + f * (ys[i] - ys[i - 1])
    return ys[-1]


def apply_cal(cal, p):
    """Calibration dispatch: Platt dicts carry a/b, isotonic carry x/y."""
    if not cal:
        return p
    if "x" in cal:
        return max(0.001, min(0.999, apply_isotonic(cal, p)))
    return apply_platt(cal, p)


# ----------------------------------------------------- drift detection


def ph_new(delta=0.005, threshold=8.0, alpha=0.999, min_n=20):
    """Page-Hinkley drift-detector state, after river's implementation
    (github.com/online-ml/river): a CUSUM on deviations from the running
    mean of a stream. When the cumulative sum strays past threshold, the
    stream's regime has changed — for us, the stream is the brain's own
    per-settle logloss, so a firing means the market stopped behaving the
    way the brain learned it."""
    return {"n": 0, "mean": 0.0, "sum": 0.0, "lo": 0.0, "hi": 0.0,
            "delta": delta, "threshold": threshold, "alpha": alpha,
            "min_n": min_n, "drifts": 0}


def ph_update(ph, v):
    """Feed one observation; True = drift detected (state self-resets)."""
    ph["n"] += 1
    ph["mean"] += (v - ph["mean"]) / ph["n"]
    ph["sum"] = ph["alpha"] * ph["sum"] + (v - ph["mean"] - ph["delta"])
    ph["lo"] = min(ph["lo"], ph["sum"])
    ph["hi"] = max(ph["hi"], ph["sum"])
    if ph["n"] >= ph["min_n"] and (ph["sum"] - ph["lo"] > ph["threshold"]
                                   or ph["hi"] - ph["sum"] > ph["threshold"]):
        ph.update({"n": 0, "mean": 0.0, "sum": 0.0, "lo": 0.0, "hi": 0.0,
                   "drifts": ph["drifts"] + 1})
        return True
    return False


# -------------------------------------------------------- diagnostics


def brier(model, data):
    return round(sum((predict(model, x) - y) ** 2 for x, y in data)
                 / len(data), 5)


def logloss(model, data):
    return round(sum(_logloss(predict(model, x), y) for x, y in data)
                 / len(data), 5)


def permutation_importance(model, data, seed=3, repeats=2):
    """Which features actually matter: shuffle one column, measure how much
    the Brier score degrades. Honest, model-agnostic, and immune to the
    'big weight on a useless feature' illusion."""
    rng = random.Random(seed)
    base = brier(model, data)
    out = {}
    feats = _keys(data)
    for f in feats:
        worst = 0.0
        for _ in range(repeats):
            col = [x[f] for x, _ in data]
            rng.shuffle(col)
            shuffled = [(dict(x, **{f: col[i]}), y)
                        for i, (x, y) in enumerate(data)]
            worst += brier(model, shuffled) - base
        out[f] = round(worst / repeats, 5)
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def calibration_table(model, data, bins=5):
    """Predicted vs realized win rate per probability bin — the lie
    detector for a probability model."""
    rows = sorted((predict(model, x), y) for x, y in data)
    out = []
    step = max(1, len(rows) // bins)
    for i in range(0, len(rows), step):
        chunk = rows[i:i + step]
        if len(chunk) < 3:
            continue
        out.append({"predicted": round(sum(p for p, _ in chunk) / len(chunk), 3),
                    "actual": round(sum(y for _, y in chunk) / len(chunk), 3),
                    "n": len(chunk)})
    return out


# ----------------------------------------------------------- registry

ZOO = {
    "gbm": fit_gbm,
    "gbm-slow": lambda d: fit_gbm(d, lr=0.07, rounds=120),
    "xgb": fit_xgb,
    "xgb-reg": lambda d: fit_xgb(d, lam=3.0, gamma=0.02),
    "forest": fit_forest,
    "forest-big": lambda d: fit_forest(d, n_trees=80),
    "mlp": fit_mlp,
}


def sgd_step(w, x, y, lr=0.05, l2=0.01, g2=None):
    """One online gradient step on a logistic model — the instant-learning
    path: every settle nudges the weights the moment it lands, between
    full championship retrains. Pass a g2 dict to enable AdaGrad: each
    feature earns its own step size lr/sqrt(sum of its squared gradients),
    so rarely-seen features still learn fast while frequently-updated ones
    settle down instead of oscillating."""
    z = sum(w.get(k, 0.0) * v for k, v in x.items())
    p = _sigmoid(z)
    for k, v in x.items():
        g = (p - y) * v + (l2 * w.get(k, 0.0) if k != "bias" else 0.0)
        if g2 is not None:
            g2[k] = round(g2.get(k, 0.0) + g * g, 6)
            step = lr / math.sqrt(g2[k] + 1e-8)
        else:
            step = lr
        w[k] = round(w.get(k, 0.0) - step * g, 5)
    return w


def self_test():
    """The library proves itself on planted problems before anyone trusts
    it with money. Run: python3 ml.py"""
    rng = random.Random(1)
    # ring problem: y = 1 iff 0.3 < v < 0.7 — linearly inseparable
    ring = [({"v": v, "u": rng.random()}, 1.0 if 0.3 < v < 0.7 else 0.0)
            for v in [rng.random() for _ in range(400)]]
    # xor-ish interaction: y = a XOR b — needs depth 2 or hidden units
    xor = [({"a": a, "b": b}, float((a > 0.5) != (b > 0.5)))
           for a, b in [(rng.random(), rng.random()) for _ in range(400)]]
    failures = []
    for name, fit in ZOO.items():
        for prob_name, prob in (("ring", ring), ("xor", xor)):
            m = fit(prob)
            acc = sum((predict(m, x) >= 0.5) == (y >= 0.5)
                      for x, y in prob) / len(prob)
            status = "PASS" if acc > 0.8 else "FAIL"
            if status == "FAIL":
                failures.append((name, prob_name, acc))
            print(f"  {status}  {name:7s} on {prob_name}: {acc:.0%}")
        imp = permutation_importance(fit(ring), ring)
        top = next(iter(imp))
        status = "PASS" if top == "v" else "FAIL"
        if status == "FAIL":
            failures.append((name, "importance", top))
        print(f"  {status}  {name:7s} importance finds the real feature ({top})")
    cal = fit_platt([(0.9, 1.0)] * 8 + [(0.9, 0.0)] * 2
                    + [(0.1, 0.0)] * 8 + [(0.1, 1.0)] * 2)
    fixed = apply_platt(cal, 0.9)
    status = "PASS" if 0.6 < fixed < 0.95 else "FAIL"
    if status == "FAIL":
        failures.append(("platt", "calibration", fixed))
    print(f"  {status}  platt calibration keeps probabilities sane ({fixed:.2f})")

    # isotonic must recover a planted monotone distortion exactly
    iso = fit_isotonic([(0.2, 0.0)] * 30 + [(0.2, 1.0)] * 10
                       + [(0.5, 0.0)] * 12 + [(0.5, 1.0)] * 28
                       + [(0.8, 0.0)] * 10 + [(0.8, 1.0)] * 30)
    mono = all(iso["y"][i] <= iso["y"][i + 1]
               for i in range(len(iso["y"]) - 1))
    a, b = apply_cal(iso, 0.2), apply_cal(iso, 0.5)
    good = mono and abs(a - 0.25) < 0.05 and abs(b - 0.70) < 0.05
    status = "PASS" if good else "FAIL"
    if not good:
        failures.append(("isotonic", "pav", (mono, a, b)))
    print(f"  {status}  isotonic PAV recovers planted rates "
          f"(0.2->{a:.2f}, 0.5->{b:.2f}, monotone={mono})")

    # page-hinkley: silent on a stationary stream, fires on a level shift
    ph = ph_new()
    quiet = not any(ph_update(ph, 0.5 + rng.uniform(-0.1, 0.1))
                    for _ in range(200))
    fired = any(ph_update(ph, 1.4 + rng.uniform(-0.1, 0.1))
                for _ in range(60))
    status = "PASS" if quiet and fired else "FAIL"
    if not (quiet and fired):
        failures.append(("page-hinkley", "drift", (quiet, fired)))
    print(f"  {status}  page-hinkley quiet when stationary, "
          f"fires on regime shift ({quiet}/{fired})")

    # adagrad sgd must converge on a planted linear rule
    w2, g2 = {}, {}
    for _ in range(400):
        v = rng.random()
        sgd_step(w2, {"bias": 1.0, "v": v - 0.5},
                 1.0 if v > 0.5 else 0.0, g2=g2)
    probe = [rng.random() for _ in range(200)]
    acc = sum((_sigmoid(w2["bias"] + w2["v"] * (v - 0.5)) >= 0.5)
              == (v > 0.5) for v in probe) / len(probe)
    status = "PASS" if acc > 0.9 else "FAIL"
    if acc <= 0.9:
        failures.append(("adagrad", "convergence", acc))
    print(f"  {status}  adagrad online learning converges ({acc:.0%})")

    print(f"\nml.py self-test: {'ALL PASS' if not failures else failures}")
    return not failures


if __name__ == "__main__":
    import sys
    sys.exit(0 if self_test() else 1)
