# Sophisticated techniques backlog — Claude evaluates one per deep review

Rules: test against data we have (journal, attribution, research, lab,
backtest) before any goes live. Verdicts move items to Tested with numbers.
A trick with no testable prediction is not a trick, it's a vibe.

## Untested
2. **Cross-market consistency arb** — related binary markets (e.g. "BTC >66k"
   and "BTC >68k") must satisfy monotonic prices; violations are free money
   like neg-risk arb. Test: scan Gamma for same-asset threshold families,
   measure violation frequency/size before building.
3. **Settlement-hour clustering** — daily markets (weather, crypto) resolve
   at known hours; prices drift toward certainty in the final 2-3h. Predict:
   entries 2-4h pre-resolution have better risk-adjusted returns than 24h+.
   Test: research data has timestamps + outcomes; bucket by hours-to-end.
4. **Maker-vs-taker spread capture** — we always cross the spread (taker).
   On 2-4c spread markets, resting a bid 1c inside could earn the spread.
   Paper-sim: assume fill if price trades through us. Test in lab first.
5. **Whale-print depth deltas** — sudden one-sided depth changes
   (book_stats deltas between checks) may front-run resolution news.
   Partially superseded by the fresh-wallet tracker (2026-06-12), which
   watches WHO prints rather than book depth; depth deltas remain untested.
6. **Stale-anchor fade** — markets that haven't repriced after a related
   market moved (e.g. team A's game total moved but spread didn't). Needs
   event-graph; expensive. Park until cross-market scan (#2) exists.
7. **Kelly with estimation-error haircut** — replace point-Kelly with
   Kelly computed at the Wilson lower bound of edge (we partly do this);
   compare growth vs. current quarter-Kelly in backtest replay.
8. **Political repeat-wallet pattern** — the third documented insider
   pattern: the same wallet repeatedly entering political markets where
   info advantages exist. wallet_intel.json now accumulates per-wallet
   profiles; once it has a week of data, cross-reference repeat wallets
   per political market vs outcomes. Predict: repeat-wallet side wins
   more often in politics than in sports/crypto (where edges are public).

## Tested
- Chart-outcome shape model (#9) -> REJECT 2026-06-12 09:05 (2,508
  labeled paths, 753 chronological holdout): model Brier 0.15469 vs
  market 0.14955, skill -0.00514. Path shape carries NO edge at
  resolution horizons — the price already knows the chart. Pairs with
  the move model's KEEP (+0.075 at 30-min horizons): shape is alpha at
  minutes-scale and fully priced at days-scale. Lesson: horizon decides
  whether a signal is information or decoration.
- Resolution-source edge (#1) -> REJECT, DIRECTIONALLY REVERSED 2026-06-12
  08:15 (530 labeled favorites 90-99c <24h spread<=4c, one obs/market):
  objective-feed (weather+crypto) 97.0% win but +0.13%/$1; judgment+sports
  97.5% win, +1.92%/$1. Substructure is the real finding: SPORTS favorites
  75/75 wins across 61 distinct families +4.72%/$1 (Wilson LB ~95.2% vs
  ~95.5% breakeven — promising, one good week short of conclusive);
  crypto-threshold NEGATIVE (-0.69%/$1, 299 obs collapsing to 24 families).
  Action: blocks now age out on a 14-day window; explorer re-tests Sports
  naturally when its 06-10/11 block expires (~06-24). Watch that date.
- Settlement-hour clustering (#3) -> KEEP-as-design 2026-06-11: explorer's
  1-24h window + EV-per-day ranking embody it; replay sim cells 95-100c
  <24h ran ~100% win (low eff-n caveat). Live explore resolutions 5/5 wins.
- RECURRING LESSON (3 instances): any model that can block a strategy from
  generating the very data that would unblock it is a self-locking trap —
  m11 bare singles, m4 explore blocks, m5 arb-mass poisoning. Rule now
  encoded: risk models govern profit books; the info book answers only to
  its budget; locked payouts carry no risk.
- ML shadow-to-power pipeline -> FIRST PROMOTION 2026-06-12 08:00: model
  15's dislike verdict proved out (10/10 settled dislikes lost, avg -$0.19,
  ~2x baseline). Granted graduated power: strong dislike (p < entry-5c)
  skips explorer entries; mild dislike (p < entry-3c) halves Kelly-book
  size. Verify: by_m15_verdict disliked bucket avg should shrink toward 0
  as gated entries stop settling.
