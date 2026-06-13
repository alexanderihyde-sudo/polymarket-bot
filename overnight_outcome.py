"""Overnight test of TRICKS #9: does chart SHAPE predict final
resolutions (not just 30-min reversions)? Trains a chart-outcome model
on resolved recorded paths, judged by Brier against the market price
itself — the m15 standard. Writes outcome_model.json + a verdict line
the morning review reads. Run: nice python3 overnight_outcome.py
"""
import csv
import json
import pathlib
import time
from datetime import datetime

import bot
import chartml
import ml


def main():
    t0 = time.time()
    series = {}
    for path in sorted(pathlib.Path("data").glob("snapshots-*.csv")):
        for r in csv.reader(open(path)):
            try:
                ts = datetime.fromisoformat(r[0]).timestamp()
                series.setdefault(r[1], []).append((ts, float(r[2])))
            except (ValueError, IndexError):
                continue
    rich = {k: v for k, v in series.items() if len(v) >= 50}
    print(f"{len(rich)} markets with 50+ recorded points "
          f"({time.time() - t0:.0f}s)")

    ids = sorted(rich)
    finals, closed_at = {}, {}
    for i in range(0, len(ids), 20):
        for m in bot.get_json(f"{bot.GAMMA}/markets",
                              params=[("id", x) for x in ids[i:i + 20]]
                              + [("closed", "true")]) or []:
            pr = [bot.fnum(x) for x in bot.jlist(m.get("outcomePrices"))]
            if len(pr) == 2:
                finals[str(m["id"])] = pr[0]
                closed_at[str(m["id"])] = (m.get("closedTime")
                                           or m.get("endDate") or "")
    print(f"{len(finals)} resolved ({time.time() - t0:.0f}s)")

    rows = []
    for mid, fin in finals.items():
        pts = rich[mid]
        x = chartml.chart_x(pts)
        if x is None:
            continue
        price = pts[-1][1]
        if not 0.05 <= price <= 0.95:
            continue            # near-certain paths teach nothing here
        x["price"] = price - 0.5
        rows.append((closed_at[mid], x, 1.0 if fin >= 0.5 else 0.0, price))
    rows.sort(key=lambda r: r[0])
    print(f"{len(rows)} labeled shape rows")
    if len(rows) < 300:
        print("VERDICT: insufficient data — rerun after more resolutions")
        return

    cut = int(len(rows) * 0.7)
    train = [(x, y) for _, x, y, _ in rows[:cut]]
    hold = rows[cut:]
    best_name, best_b, model = None, None, None
    for name in ("gbm", "xgb", "forest"):
        try:
            m = ml.ZOO[name](train)
            b = sum((ml.predict(m, x) - y) ** 2
                    for _, x, y, _ in hold) / len(hold)
            if best_b is None or b < best_b:
                best_name, best_b, model = name, b, m
        except Exception as e:
            print(f"  {name} failed: {e}")
    b_market = sum((p - y) ** 2 for _, _, y, p in hold) / len(hold)
    skill = round(b_market - best_b, 5)
    out = {"generated": bot.now_utc().isoformat(timespec="seconds"),
           "n_train": len(train), "n_holdout": len(hold),
           "champion": best_name,
           "brier_model": round(best_b, 5),
           "brier_market": round(b_market, 5),
           "skill_vs_market": skill,
           "verdict": "KEEP — shape beats price" if skill > 0
                      else "REJECT — price already knows the shape",
           "model": model if skill > 0 else None,
           "mode": "shadow — never acted on without a review decision"}
    json.dump(out, open("outcome_model.json", "w"))
    print(f"VERDICT TRICKS#9: {out['verdict']} | model Brier {best_b:.5f} "
          f"vs market {b_market:.5f} | skill {skill:+.5f} | "
          f"holdout {len(hold)} | {time.time() - t0:.0f}s total")


if __name__ == "__main__":
    main()
