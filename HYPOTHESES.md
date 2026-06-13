# Hypothesis backlog — each 2h review tests the top untested one, records the
# verdict here (KEEP/REJECT + numbers), and adds at least one new idea.

## Untested
1. Weather daily markets behave differently from the backtest population —
   compute favorites win rate for weather vs non-weather once 20+ settle.
4. Arbitrage edge size vs realization — do small-edge arbs (1.5-2c) actually
   settle at full profit? Check after 5+ arb settlements.
6. Favorites at 93c (isolated positive band, n=136, +1.9% edge) — test as a
   second allowed band in a lab-style replay before ever trading it live.

7. Lab hourly fidelity is blind to intra-hour sports moves — rerun lab with
   sports excluded (or fidelity=10) to see if fade's +2.7c edge survives.
8. Fade may only work OUTSIDE Sports — compare fade results by category once
   non-sports fades settle (Sports already auto-blocked by category learning).

1b. Crypto-THRESHOLD favorites are a negative bucket: research instrument
   says -0.69%/$1 (n=299, only 24 families) and live Crypto swung +2.11 ->
   -1.66 today. Predict: hp Crypto material settles go net negative within
   30 days; if so the category learner blocks it — if it does NOT, the
   research instrument has a confound to find.

9. INSTRUMENT CONTRADICTION (must explain): research said sports
   favorites 90-99c <24h won 75/75 across 61 families; live lane90 sports
   went 0/5 (-50.56), all in-game gap-deaths, same day. P(0/5 | 97% win)
   ~ 3e-8 — the instrument is WRONG for sports. Suspects: (a) one-obs-
   per-market hides intra-game re-crossings of 90c (a market that gaps
   and recovers gets counted once, at a lucky snapshot); (b) survivorship
   in which markets the recorder catches at 90c+. Test: per-market, count
   how many distinct times price crossed 90c from below in tick memory;
   markets with multiple crossings = whipsaw population the instrument
   undercounts. Until explained, NO research-seeded sports lanes.

## Tested
- Spread at news entry (#3): REJECT 2026-06-12 15:10 — wide>2c lost LESS
  (-0.623/9) than tight<=2c (-0.803/15). No protective signal; news was
  broken regardless of spread. Closed (news already quarantined x0.5).
- Time-of-day (#5) within-strategy (explorer only, n=100): 00h -0.050/38,
  06h +0.002/22, 12h -0.030/27, 18h -0.210/13 -> NO ACTIONABLE SIGNAL
  2026-06-12 (18h worst but n=13 and the era confound dominates). Closed.
- 2026-06-11: follow-the-move momentum (8c & 12c): REJECT (-0.4 to -0.7c/share,
  n=222). Fade-the-move: KEEP (+2.2 to +2.7c/share, n=242). Fade 12c promoted.
- 2026-06-11 03:45: book imbalance at entry predicts news outcomes: REJECT —
  with n=22 settled, high-imbalance entries (>=0.5) lost MORE (-$9.54/15) than
  low (-$2.35/7). No protective signal; directionally opposite to hypothesis.
- 2026-06-11: judging news fade by old follow-mode record: REJECT (evidence
  contamination) — learning rules made mode-aware.
- Explorer low-band losses are stop-churn artifacts, not edge evidence ->
  CONFIRMED 2026-06-11: 28 entries <=92c, 25 stopped out (-$1.26), 0 held to
  resolution; entries 85-88c sat on the global 85c stop. Fixed: explorer now
  uses stop = entry-0.12, target 0.995 (hold for the label).
- Relative stops produce resolution labels -> CONFIRMED 2026-06-11 18:00:
  first 2 true explorer resolutions ever (both wins, +$0.12); favorites still
  0 resolutions in 11 settles (all insurance exits) — material-evidence rule
  shipped so insurance can't drive sizing/blocks.
- Book-refill after material-evidence rule -> REFUTED-AS-STATED 2026-06-11
  20:00: book fell to 19 because model 11 minted a bare 'strat=explore' veto
  (43 entries blocked) from stop-artifact losses. Root cause was veto scope,
  not category blocks. Fixed: pairs-only vetoes + explorer exempt (it IS the
  shadow-tester). Re-test refill next review.
- NEW: explorer resolutions are 3/3 wins while its stop-outs were all losses;
  hypothesis — with stops removed, explore daily P&L turns positive within
  48h as resolution rate rises. Check with attribution by_exit.
- OOS RESEARCH FINDING (corrected instrument, 873 labeled, 2026-06-12 00:10):
  with our actual quality gates (spread<=4c) and TRUE time-to-resolution,
  90-93c favorites return +2.7% to +8.5% per $1 (win 93.5-97.8%, n=42-49
  per band); 94-99c is ~flat (-1.2% to +0.8%) — efficiently priced. The
  earlier 'catastrophic 98-99c' table was 100% confound (wide books +
  mid-game obs mislabeled as 0-6h entries). HYPOTHESIS: explorer's 90-93c
  resolutions will qualify that band for promotion within days; the main
  book's 96-98.9c zone is breakeven-at-best live. Probation pipeline is the
  designated judge — no manual band change.
- m15 gate -> explorer >=50% better: KEEP, EXCEEDED 2026-06-12 07:45 —
  last-30 explorer avg +0.0167/settle vs -0.085 baseline (flipped positive;
  14 take-profits, 9 resolutions, 7 model-exits, 0 stop-outs in window).
- NEW: post-stop daytrade re-entry is negative EV — baseline n=2 (SpaceX
  IPO: -6.09 stop then -1.17 open repeat 30min later). 6h cooldown shipped
  07:45; verify no same-market daytrade pairs <6h apart appear in journal.
