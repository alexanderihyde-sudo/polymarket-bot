# Quant Review Log

## 2026-06-11 ~03:15 UTC — review #1 (manual run)

**Account**: $995-ish total, 16 open, realized –$5.17 (all from news strategy).

**Strategy verdicts**
- Arbitrage: 1 catch (NBA Champion, ~+$0.29 locked), 0 settled. Working, opportunities rare. Healthy.
- Favorites (high_prob): 0 settled yet (24–48h window means first resolutions land Jun 12). ~15 open, mostly weather/crypto at 97–99¢. No verdict until they settle.
- News: **–$5.17 over 16 settled** → learning correctly paused it. BUT: losses were almost entirely from the old "follow" mode; the lab (400-market replay) showed follow loses (–0.4¢/share) and fade wins (+2.7¢/share at 12¢ trigger).

**Change made (evidence-gated)**: pause/halve rules for news are now mode-aware —
the fade mode is judged only on fade trades, not the dead follow mode's record.
Fade restarts with a clean slate ("gathering data, 0/8") and the standard
guardrails: pause again after 16 net-losing trades of its own.

**Research**: 565 deduped observations recorded; 0 resolved yet (recorder is
hours old). Expect first labeled results within ~24h.

**Honest concerns to watch next reviews**
1. Favorites positions are concentrated in daily weather markets — these are
   high-frequency, may behave differently from the backtest population.
2. News fade has positive lab evidence but zero live evidence; watch its first 8.
3. The -$5.17 news lesson cost ~0.5% of bankroll — acceptable tuition.

Next review: automatic, every 2 hours at :41.

## 2026-06-11 ~03:45 UTC — review #2 (automated)

- Account ~$989.3, 20 open, realized -$11.89. Bot + watchdog alive.
- **Live fade is underperforming its lab result**: 6 settled, -$6.72 (lab said
  +2.7c/share). Cause hypothesis: losses concentrated in in-game Sports moves,
  which hourly-fidelity lab paths can't see. Category learning already
  auto-blocked Sports for news at 03:21. No manual override — multiplier rule
  reacts on its own at 8 settled if bleed continues.
- Favorites: 0 settled yet (first wave resolves Jun 12); 18 open. Arb: 1 open, 0 settled.
- Hypothesis tested: imbalance-at-entry as news edge predictor -> REJECT (n=22,
  high-imbalance lost more). Removed the assumption from backlog; added two new
  hypotheses (lab fidelity blindness; fade-outside-sports).
- Research: 600 obs/375 markets recorded; 0 resolved yet.

## 2026-06-11 ~04:25 UTC — review #3 (framework application)

- Added risk analytics: Monte Carlo VaR/CVaR (4,000 paths, intra-cluster
  correlation via shared cluster shocks, Wilson-bound win probs) + cluster
  stress scenarios. First report: expected book P&L +$1.74, VaR95 $4.56,
  CVaR95 $6.40, absolute worst case -$36.56 (3.7% of bankroll — acceptable).
  Biggest stress: all-weather-fail -$23.56, exactly the concentration flagged
  in review #1; model 5 cluster cap now limits further weather stacking.
- 10-model overlay live since ~04:15: model 9 already exited Taipei weather
  favorite at 87c for -$0.23 (vs riding to the 85c stop). Model 4 blocks
  00-06h UTC entries (22 settles there, net loss). Equity-trend + Bayes
  throttles: hp sizing x0.5, news x0.25.
- Research: 643 obs, 0 labeled yet — first real out-of-sample labels arrive
  with Jun 12 resolutions. Walk-forward note: evolver re-runs optimize+lab
  every 6h on fresh windows = rolling re-validation.

## 2026-06-11 ~05:20 UTC — external review response
- Raised explorer->main promotion bar from 15 to 25 settles per band (15
  cannot separate p=0.95 from p=0.88; promotion moves Kelly-sized money).
  Noted: promotion only widens the scan band — Kelly sizing remains an
  independent evidence gate. Pattern-miner 'discoveries' acknowledged as
  correlated slices of the same ~24 news settles, not independent findings.

## 2026-06-11 ~05:45 UTC — promotion is no longer a one-way door
- Accepted external critique in full: qualifying settles are tainted by
  winner's curse (best of ~14 bands) + optional stopping (6h re-peeks), and
  Kelly amplifies an inflated p. Promoted bands now enter PROBATION: quarter
  size, tagged context.probation, judged ONLY on clean post-promotion
  settles (n>=15 Wilson>breakeven & pnl>0 -> graduate to full size;
  pnl<-0.50 with Wilson<breakeven -> auto-demote to explorer duty).
  One probation at a time. probation_verdict() unit-tested (32/32).
- Runner-up adopted: ttr (<24h/1-3d/>3d) added to miner traits; candidates
  now ranked by EV-per-day (capital velocity), arbs always first.

## 2026-06-11 ~14:10 UTC — THROUGHPUT UPGRADE shipped (infrastructure, exempt)
- REPLAY ENGINE live: 470,631 recorded ticks -> 423 walk-forward sim trades
  in 22s (1,137/min, target 1,000+ met). SIM ledger separate (sim_results
  .json); proposes only. First candidate beliefs: 95-100c favorites <24h
  ~100% win rate (matches backtest; eff-n still tiny, 6 clusters).
- CRITICAL BUG found by replay work: Gamma drops closed markets from plain
  id queries -> settle_positions could NEVER settle a resolved market
  (positions would hang forever). Fixed with closed=true re-probe; first
  stuck settles flowed within minutes of restart.
- Governor: 8 calls/s sustained, 16 burst, 30 orders/min ceiling, 429
  exponential backoff, shared across all consumers.
- Effective-n everywhere: credibility (brain n/(n+60) on clusters),
  promotion needs 15+ clusters AND 25 raw, dashboards show raw/eff.
  Live sample: raw 61 = eff 28 clusters.
- Explorer: 200-position cap, 1-24h fast-settle window, max 3 open per
  (category x 5c-band) cell, 30-min re-entry cool-off. Recorder 4->8 pages.
- Tests 44/44. Settles last 24h: 61 (vs ~26 in the prior ~12h).

## 2026-06-11 ~16:15 UTC — head-quant review #4
- Verdicts (80 settled, eff 35): news -16.69/23 = the only real bleeder,
  fully throttled (x0.25, Sports+00-06h blocked) — effectively shelved.
  Favorites -0.28/7, all tiny protective exits; thesis still unjudged (20
  open). Explorer -1.08/50 = cheap data, BUT 25 of its 28 low-band settles
  were stop-churn artifacts (entries 85-88c vs global 85c stop): 0 labels
  earned, and the artifact losses wrongly triggered explore category blocks
  (Weather/Crypto/Sports/Highest temperature) -> book drained 46->34 open.
- Attribution: take-profits +5.45/24, stop-losses -18.18/46 (news-dominated),
  model-exits -0.53/8 (cheap insurance). brain/thompson buckets still n=10,
  too early to judge.
- SHIPPED (one strategy change): explorer exits are now relative — stop
  entry-0.12 (catastrophe only), target 0.995 — explorer holds to
  resolution because the LABEL is the product. Hypothesis logged: low bands
  should now produce resolution labels and category blocks should heal.
- Instrumentation repair (not a strategy change): research() had the same
  closed-market query bug as settle — fixed; labels should flow next cycle.
- Watch next review: research labeled count > 0; explore resolution-label
  rate at 85-92c; whether artifact category blocks heal; brain_adj buckets
  filling. 44/44 tests, restart clean, books balanced.

## 2026-06-11 ~16:40 UTC — post-mortem engine (user: 'figure out WHY')
- bot.py postmortem: classifies every losing settle from price action —
  churn / fast-stop / gap-death / orderly-bleed / wrong-thesis — and for
  recent stop-outs with tokens, fetches the post-exit chart to label
  whipsaw (recovered = stop too tight) vs vindicated (kept falling).
- Autopsy of all 55 losers: news fast-stop 14/-10.82, news gap-death
  4/-5.80, news wrong-thesis 1/-4.80 -> EVERY major loss = fading an
  in-game MLB market: jump-process prices make tight stops fictional
  (4 gapped straight through). Explore churn 32/-1.69 (already fixed).
- CAUSAL FIX shipped: news.min_hours_to_resolution=6 — in-game markets all
  resolve within hours; one missing gate admitted the whole trap class.
  Settled records now carry exit_price/stop/token/path for future autopsies.
- Deep-review protocol upgraded: postmortem now step 1 alongside
  attribution (cron 4afc1bb3). 44/44 tests, restart clean.

## 2026-06-11 ~18:00 UTC — head-quant review #5
- THE FINDING: favorites halved by streak rule on 11 settles containing ZERO
  resolutions — all protective exits totaling -$0.69. Insurance premiums
  were being read as strategy failure (same artifact class as explorer
  stop-churn). Explorer earned its first 2 TRUE resolution labels ever
  (both wins) — review #4's relative-stop fix confirmed working.
- SHIPPED: material-evidence rule — learning streaks AND band/category
  blocks now count only settles with |pnl| >= $0.15. Effects on restart:
  favorites restored to full size ('2 of 11 material'); explorer's four
  artifact category blocks (Weather/Crypto/Sports/Highest temperature)
  dissolved -> book should refill. Test added: insurance can't halve a
  strategy. 45/45.
- Postmortem unchanged story: news in-game trap (gated), explore churn
  (fixed), hp losses all insurance-class.
- Watch next review: book refill toward 50+, resolution-label rate >5/day,
  research labeled count (still 0 — recorder markets resolve on multi-day
  horizons), first favorites TRUE resolutions.

## 2026-06-11 ~19:05 UTC — Brain 2.0 + research-backed pairs strategy
- Research (QuantPedia/IMDEA/SSRN): strongest documented edges = extreme-price
  favorite-longshot, combinatorial/intra-market arb ($40M+/yr extracted on
  Polymarket, windows ~200ms), informed flow near resolution. Two of three
  already ours; gap = combinatorial structures beyond neg-risk.
- SHIPPED A: threshold-pair arbitrage — same-family monotonicity violations
  (P(X>66k) < P(X>68k)) -> buy YES(low)+NO(high), min payout $1/share,
  locked. Strict parsing, 1.5c safety margin over min edge, runs every pass.
- SHIPPED B: Brain 2.0 — ensemble (global + per-strategy specialists),
  cluster random-effects + interaction features, and built-in walk-forward:
  75% chronological train / 25% holdout vs base-rate baseline; credibility
  = cluster-eff-n x OOS-skill factor.
- FIRST RESULT IS THE HEADLINE: brain v1 had NEGATIVE out-of-sample skill
  (-0.143, 21.7% acc on last 23) — it was tilting sizes on in-sample fit.
  Brain 2.0's first act was demoting itself to minimum voice (x0.25).
  Watch: skill recovering as resolution-quality labels accumulate.
- 51/51 tests. Restart clean, books balanced.

## 2026-06-11 ~19:35 UTC — capital + exit shift (user: 'it's losing, shift')
- Diagnosis: recent bleed = 6 explorer stop-outs -$3.72, avg -$0.62 each =
  GAPS through the relative stop (weather forecasts jump like in-game
  scores). Holders won: explore resolutions 3/3 wins, hp take-profits +.
- Shift 1: explorer stops REMOVED (stop 0.02 sentinel) — a $1 stake is its
  own insurance; stops only converted wobbles into certain losses. Target
  0.995 kept to lock near-certainties.
- Shift 2: allocations news 2000->500 (shelved strategy), arbitrage ->4500,
  favorites ->4000 (the validated edge + the locked-profit book get the
  capital). Explore stays 1000.
- 51/51 tests, restart clean.

## 2026-06-11 ~20:05 UTC — head-quant review #6
- Verdicts (99 settled, eff 45): news -16.69 quarantined (no trades since
  gates — working). hp -0.65 all insurance, 0 true resolutions yet, full
  size under material rule. explore -4.60: stop-gap artifacts (fixed 19:35,
  stops removed); its RESOLUTIONS remain 3/3 wins. arb +2.08 locked, 3 open.
- ROOT CAUSE of book drain (19 open): model 11 minted a bare 'strat=explore'
  kill-switch veto from artifact losses — 43 entries blocked. Same blunt-
  single disease as the old hour=00. SHIPPED: vetoes must be pairs AND the
  explorer is exempt from model 11 entirely (it is the shadow-tester; budget
  + material cell blocks + Thompson govern it). Test pins pairs-only. 52/52.
- Hypothesis ledger: book-refill prediction REFUTED-AS-STATED (cause found
  elsewhere); new hypothesis: stop-free explorer turns daily-positive in 48h.
- Brain 2.0 note: skill_factor 0.25 (negative OOS skill) — correctly muted.
- Watch next review: book refill (target 40+), explore resolution count and
  P&L sign, first hp TRUE resolutions, pairs-arb first catch.

## 2026-06-11 ~20:10 UTC — model 15: ML on the big dataset (user departing)
- SHIPPED: market-outcome model trained on 1,806 labeled observations from
  thousands of recorder markets (vs ~100 own trades) — walk-forward, judged
  vs the market price itself (Brier). First run: model 0.218 vs market
  0.190 -> skill -0.028: the market still forecasts better than our model,
  exactly why it runs SHADOW-ONLY (m15_p recorded on every entry, never
  acted on). It earns action rights only when attribution shows trades it
  disliked lose more (by_m15_verdict bucket added).
- Evolver now retrains it every 6h as the corpus grows (850k+ ticks, +400
  markets/day). The path to alpha: FLB 'extreme' feature + growing data.
- Autonomy handoff: mini每5m (03a361e4), deep每2h (4afc1bb3) each ship one
  evidence-gated improvement, evolver每6h (optimize+lab+promotion+ML).
  52/52 tests, books balanced.

## 2026-06-11 ~22:05 UTC — head-quant review #7
- Explorer starvation root-caused, TWO self-locking artifacts: (1) m5 had
  vetoed 369 entries because the $84.71 locked-payout arb position counted
  as 'other'-cluster risk mass (zero outcome risk!); (2) m4 minted an
  explore 18-24h block from stop-era losses — and 22:00 UTC is inside it,
  with no way to gather healing evidence while blocked. Third instance of
  the self-locking disease; principle now encoded + tested (54/54): locked
  arbs carry no cluster risk, m4 never grounds the info book.
- Attribution: take-profit +5.64/32 vs stop-loss -21.90/52 (legacy-heavy);
  model-exits -1.06/13 insurance. m15 shadow bucket empty (entries too
  fresh) — check next review. Thompson low-draw avg -0.009 (neutral).
- explore resolutions now 5/5 wins. hp 15 settles still 0 true resolutions.
- TRICKS: settlement-hour clustering -> Tested/KEEP-as-design; recurring
  self-locking lesson codified.
- Watch next: explorer refill within the hour (both chokers removed), m15
  verdict bucket fills, first hp resolutions, news stays quiet.

## 2026-06-12 ~00:10 UTC — head-quant review #8
- Book refilled as predicted: 41 open (18 -> 41), explore o20 — review #7
  chokers confirmed dead. explore resolutions still perfect record.
- HEADLINE: first at-scale OOS labels (1,991 raw). Naive table showed
  98-99c favorites at -24/-28%% ROI — but it contradicted replay on the
  SAME data. SHIPPED (the one change): research instrument corrected —
  (a) spread<=4c filter to measure only the universe we trade, (b) re-
  bucket by TRUE close time (endDate lies for early resolvers; ' 0-6h'
  obs were sometimes MID-GAME), (c) drop obs after true close. 54/54.
- CORRECTED RESULT (873 labeled): 90-93c = +2.7%..+8.5% per $1 (the edge
  zone); 94-99c flat. The catastrophe was pure confound. Explorer already
  trades 85-99c; promotion/probation pipeline is the judge for moving the
  main book down — no manual override.
- m15 shadow bucket still empty (no tagged settles yet). Watch next:
  m15 fills, explorer 90-93c resolution count toward the 25-cluster
  promotion bar, first hp true resolutions, news quiet.

## 2026-06-12 ~02:05 UTC — head-quant review #9
- Stable tape: $9,979.91, 44 open, 107 settled (eff 49). Explore post-fix
  wave (21 open) maturing; resolutions 4/4 wins; 90-93c promotion progress
  2/25 raw, 2/15 clusters. hp STILL 0 true resolutions in 19 settles — its
  15 model-exits recycle positions before the thesis can be tested.
- MEASURED (post-exit charts, tokens now stored): model-exit whipsaw rate
  5/7 (71%) — the 93c edge-gone trigger was churning winners. SHIPPED: edge-
  gone loosened 93c -> 90c (stop at 85c unchanged). Expect fewer hp churn
  exits and finally some hp RESOLUTIONS. 54/54 tests.
- m15 confirmed tagging entries (0.667/0.678); verdict bucket fills as they
  settle. news quiet (gates holding). Watch next: first hp resolutions,
  m15 bucket, explore resolution wave, whipsaw rate at the new threshold.

## 2026-06-12 ~03:15 UTC — model 15 v2: nonlinear ML + model selection
- Research showed the edge map is NONLINEAR in price (90-93c +, 94-99c flat)
  — a shape no logistic can represent. SHIPPED: AdaBoost decision stumps
  (pure Python) + band-bucket features + automatic MODEL SELECTION by OOS
  Brier (the harness crowns the champion; no human preference).
- RESULT: stumps beat logistic OOS 0.2027 vs 0.2287; champion=stumps, and
  its top splits use the b90_93 bucket — it independently found the edge
  band. Gap to the market's own forecast closed from -0.0276 to -0.0039
  (86% closed in one upgrade). Still shadow-only until skill > 0.
- Tests prove the class difference: stumps 85%+ on a planted ring pattern
  where logistic scores <75%. 56/56. Evolver retrains both every 6h.
- Watch: skill_vs_market crossing zero as labels accumulate; m15 attribution
  bucket; first hp resolutions.

## 2026-06-12 ~03:40 UTC — day-trading desk added (user request)
- New 5th strategy 'daytrade': continuous intraday mean-reversion — fades
  8c+ 1h moves on liquid markets (vol>=25k), $10 brackets (stop 5c /
  target 8c), never holds to resolution, exits within hours. Evidence base:
  lab fade +2.2c/share at 8c threshold (400 markets); in-game trap excluded
  by the 6h-ttr gate it inherits. Scans EVERY pass; models 10/12 watch its
  positions every second. $500 allocation (carved from arbitrage),
  separate sub-account/learning/chart line for clean attribution.
- All standard governance applies from birth: material-evidence learning,
  bayes/vol throttles, pattern vetoes, daily circuit breaker. 56/56 tests,
  restart clean, desk live (dayt v500 o0 done0).

## 2026-06-12 ~04:00 UTC — the chartist: day trader reads real charts
- Day trader upgraded: every candidate's actual 6h intraday chart is fetched
  and classified by defined rules — spike_fade (anomalous move already
  reverting, judged at the spike's extreme), mean_dev (2-sigma stretch from
  own mean), breakout (pinned at a REAL range extreme, never faded), drift
  (no trade). Only fade-class patterns are tradeable (lab-validated edge).
- Learning wiring: chart_pattern + z + retrace + range_pos stored in every
  entry context -> miner learns pattern x context pairs, brain gains
  zdev/rpos features, attribution gains by_pattern bucket (Claude judges
  each pattern on settled money every review).
- Tests caught two real classifier bugs before deployment (noise 'breakout'
  with no magnitude floor; z measured after the pullback) — fixed, 60/60.
- Watch: first chartist entries, by_pattern bucket filling, whether
  spike_fade beats mean_dev on settles.

## 2026-06-12 ~04:30 UTC — real-time price engine
- POST /books bulk endpoint discovered + wired: all open books in 1 round
  trip (was 40 calls x 100ms sleeps). check_exits sweep ~25s -> <1s.
- SCHEDULER BUG FIXED: next_scan stamped before the (multi-minute) scan ->
  scans ran back-to-back, the 1s monitor loop NEVER executed; position
  prices only updated once per scan. Now: scan every 2min, monitor owns the
  gaps — every position re-priced ~1-2s, exit models 9/10/12 firing on
  second-level data. Dashboard refresh 5s -> 2s. 60/60 tests.
- Verified live: positions re-priced within a 3-second window post-fix.
- Honest ceiling: ~1s via governed REST; millisecond data = WebSocket feed,
  parked in TRICKS until paper results justify it.
## 2026-06-12 ~04:45 UTC — mini ALERT: Exact Score explore position briefly marked -0.37 (mid dip; last back at 0.98). Stop-free $0.98 explore stake = bounded by design; live game volatility, holding for the label. Bot healthy, no action.

## 2026-06-12 ~05:05 UTC — dual feed: ESPN scores racing the market
- Second data feed live: ESPN public scoreboards (soccer World Cup, MLB,
  NBA, NHL) polled ~12s, shadow mode. Every score change journaled; if we
  hold a matching market, a latency probe arms and the 1s price monitor
  records how many seconds the market took to reprice >=3c. 'Which is
  faster' becomes a measured number (SCORES line in brief; LATENCY events
  in journal). Currently watching the live KOR-CZE game we hold.
- Promotion path (evidence-gated): if median lead is seconds+, deep review
  may grant the daytrade desk a score-event entry trigger — trading WITH
  fast information, mechanically different from the banned in-game fades.
- Matcher unit-tested (Korea Republic<->South Korea naming). 62/62.

## 2026-06-12 ~05:25 UTC — score feeds: web-wide install, 3 sources racing
- Probed 5 candidate feeds across the web: ESPN OK, MLB StatsAPI (official)
  OK, NHL api-web (official) OK; Sofascore + NBA CDN block server clients
  (403) — honestly skipped, no scraping games.
- Multi-source pipeline live: order-independent score fingerprints match
  the same game across feeds despite naming/home-away differences (unit
  tested: 'South Korea' == 'Korea Republic FC'). First source to report a
  change wins the 'firsts' counter; laggards journaled as SOURCE_LAG with
  seconds; held markets still get market-latency probes. Brief SCORES line
  now shows per-source win counts. 63/63 tests.

## 2026-06-12 ~05:50 UTC — news feeds installed (move discriminator)
- Google News (top + business) + BBC World RSS polled every 2 min, no keys;
  109 headlines cached within the first cycle. Every news/daytrade entry now
  carries news_backed: does a fresh headline share 2+ subject tokens with
  the market? Literature: noise moves revert (fade = our edge), news-backed
  moves drift (fading them is how fades die) — the flag lets the miner
  (newsbk= trait), the brain, and attribution (by_news_backed) separate the
  two on settled money. Shadow first; gate later if the split is real.
- Tests caught a tokenizer bug pre-deploy (commas destroyed '$70,000').
  65/65.

## 2026-06-12 ~06:10 UTC — fundamental oracles installed (top APIs survey)
- Surveyed: Open-Meteo OK (free weather forecasts — our largest market
  category gets a FUNDAMENTAL signal); Binance geo-blocked -> Coinbase
  public spot OK (crypto strikes); Kalshi cross-venue parked (matching
  cost); FRED needs key. Installed the two winners.
- Every favorites/explore entry on weather or crypto now carries
  oracle_agree + oracle_margin: does the actual forecast / actual spot
  price agree with the side we're buying? Shadow: miner trait oracle=,
  attribution by_oracle. Promotion path: if 'disagree' trades lose
  reliably, the oracle earns a veto — measured, not assumed.
- Caches: geocode permanent, forecast 15min, spot 60s — governor-friendly.
  68/68 tests, restart clean.

## 2026-06-12 ~06:30 UTC — feeds wired into the ML core
- Brain 2.0 feature vector extended: oracle_agree (+1/-1/0), oracle_margin,
  news_backed now train alongside price/book/chart features on EVERY pass —
  the ensemble (global + specialists) learns how much weight the weather
  forecast, spot price, and headline feeds deserve, credibility still gated
  by clusters x OOS skill. Old settles default new features to 0.
- Test pins it: planted oracle-discriminated settles -> brain oracle weight
  > 0.3. 69/69 tests.

## 2026-06-12 ~08:00 UTC — head-quant review #10: first ML promotion
- THE EVENT: by_m15_verdict filled one-sided — ALL 10 settled trades the
  shadow market-model disliked lost (avg -$0.187, ~2x explorer baseline).
  Per the shadow contract, SHIPPED its promotion: graduated gate (strong
  dislike skips $1 explorer entries; mild dislike halves Kelly sizing).
  Self-locking check passed: m15 trains on recorder labels (1,012 now),
  independent of our trade flow. 69/69 tests.
- Tape: $9,979, 45 open, 119 settled (eff 57), 97 settles/24h. take-profits
  +6.04/39 and growing; news quarantined; dayt awaiting first qualifying
  move; arb +2.82 locked across 4 (new catch overnight).
- Brain OOS skill positive (+0.023, voice 59%). Learning curve panel live:
  rolling-20 at -0.105/trade, improving from -0.50 era.
- Watch next: disliked-bucket avg shrinking, explore pnl/settle vs -0.085
  baseline, dayt first entries, hp first TRUE resolutions.

## 2026-06-12 ~09:10 UTC — trend analysis + plan re-adaptation
- TRAJECTORY (avg pnl per 25-settle block): -0.49 -> -0.04 -> -0.20 -> -0.14
  -> -0.09. Reading: news era catastrophic, each structural fix bent the
  curve; current era one fix from breakeven. Brain skill oscillating at
  zero (voice 0.49); m15 -0.004 from beating the market; promotion 4/25
  raw 4/15 clusters; scores feed quiet overnight (no live games).
- RE-PLAN: the 3 documented edges are extreme-favorites (running),
  combinatorial arb (running), informed flow (MISSING) -> SHIPPED whale
  tape via data-api.polymarket.com/trades: net aggressor $ flow + big-trade
  count per candidate, whale_agree shadow-tagged into context/miner/brain/
  attribution (by_whale). Same promotion contract as m15.
- Config: daytrade vol floor 25k->10k (0 trades in 6h = funnel too narrow).
- 72/72 tests. Watch: by_whale bucket, daytrade first trades, block-6
  avg pnl (target: first positive block), promotion clusters.

## 2026-06-12 ~09:45 UTC — crypto pricing oracle + news breadth
- CRYPTO UPGRADE: 'BTC above $X' markets are digital options — now priced
  as such. crypto_prob(): Coinbase spot x Kraken realized hourly vol (168
  candles, free/keyless; Deribit DVOL probe failed) -> driftless lognormal
  P(spot_T > strike). oracle_agree/margin now carry a MODEL PROBABILITY
  edge (p_side - 0.5), abstaining inside +/-5pts. Tests: ATM = coin flip,
  far strike < 2%. Same shadow->promotion contract.
- NEWS BREADTH: Hacker News (Algolia API) joins Google+BBC in the headline
  cache — covers the Tech/SpaceX markets the RSS feeds miss.
- 74/74 tests. Watch: by_oracle bucket sharpness vs the old sign-check era,
  whale + daytrade first settles.

## 2026-06-12 ~10:45 UTC — data-driven: explorer fully hold-to-resolution
- Attribution named the active bleed: model-exits n=25 -$2.72 (avg -0.109),
  growing — recent ones are model-12 slide exits on $1 explorer stakes in
  live games, while untouched explorer positions resolve 13/13 (+$0.62).
  SHIPPED: explorer exempt from model 12 (last exit mechanism on the info
  book besides its 99.5c target). Same logic as the stop removal, which
  the resolution record vindicated.
- m15 gate vindicated by data: disliked-bucket avg improved -0.187 ->
  -0.069 since the gate went live. by_whale collecting (n=1).
- Watch: model-exit bucket stops growing; explore resolution streak; first
  m15 'liked' settles (n=2, flat so far).

## 2026-06-12 ~11:15 UTC — Brain 3.0 (user: 'ML 10x smarter')
- Four multipliers: (1) auto feature engineering — miner's top combos become
  brain inputs (first run self-selected sports/news patterns); (2) 5-fold
  walk-forward CV replaces single split (kills skill flapping); (3) L2
  chosen by CV each retrain (picked 0.15 over hardcoded 0.05); (4) temporal
  prior — 30% of previous brain blended in, knowledge accumulates.
- FIRST RESULT: cv_skill +0.0249 POSITIVE on the most conservative
  measure yet (n=137). skill ledger now records cv_skill. 76/76 tests.
- Earlier same-day ML upgrades: m15 stumps champion (OOS 0.2027 vs logistic
  0.2287), m15 gate live, crypto digital-option pricing oracle.

## 2026-06-12 ~11:45 UTC — ML-APIs article applied (Moesif survey)
- Survey verdict: all 17 listed APIs need keys/accounts, ~all paid;
  vision/speech irrelevant. Adopted the one relevant capability —
  sentiment NLP — keyless: lexicon scorer over the live headline cache.
  news_backed (binary) -> news_sent (-1..+1 directional) on every
  news/daytrade entry; brain feature nsent, miner trait sent=, attribution
  by_sentiment. Article's #1 (LLM) already fulfilled: Claude is the head
  quant. 79/79 tests.

## 2026-06-12 ~13:05 UTC — bigger + smarter sizing
- BIGGER (where evidence permits): hp Kelly ceiling $10->$25 (quarter-
  Kelly on a $10k book was being throttled by the cap, not by edge); arb
  $1,500->$2,500 per catch (locked payouts, zero losses ever, depth-
  limited anyway). Explore stays $1 (information), daytrade $10 (no
  evidence yet), news quarantined.
- SMARTER: bounded sizing bonuses (x1.25 each, capped at ceiling) when
  validated signals strongly agree — m15 LIKE (its dislike side proved out
  on 23 settles) and strong fundamental-oracle agreement (forecast >=2C of
  room / priced crypto prob >=65%). Both instantly measurable via existing
  by_m15/by_oracle buckets; demote-able same day if buckets disagree.
- Sizing stack now: Kelly(band stats) x brain(forest champion, voice 73%)
  x probation x m15 x oracle x throttles, ceiling $25. 83/83 tests.

## 2026-06-12 ~13:30 UTC — day trading at seconds cadence
- daytrade_loop thread: ~100-market liquid watchlist (refreshed 5min, in-
  game excluded), bulk-priced every 15s via POST /books (1 call), own tick
  memory detects 3c+ moves within 5min — entries fire within seconds of
  the overreaction instead of waiting for 2-min scans of an HOURLY field.
  Full pipeline kept: chart read (spike_fade/mean_dev only), spread/
  imbalance gates, brain tilt, budget/learning/REENTRY, 5c/8c brackets,
  ACCOUNT_LOCK. Exits were already 1s; the desk is now fast end-to-end.
- 83/83 tests.

## 2026-06-12 ~14:00 UTC — ML hardening pack (stack/calibrate/explain)
- Brain 5.0: COMMITTEE replaces lone champion — every model class with
  positive CV skill joins a skill-weighted stack; Platt calibration fit on
  held-out stack predictions and applied live; permutation importance of
  the true champion persisted + shown (dashboard no longer implies the
  logistic rules when the forest does).
- m15: full zoo championship (logistic/stumps/gbm/forest by OOS Brier) +
  champion model persisted; evolver retrains 6-hourly.
- 85/85 tests.

## 2026-06-12 ~14:20 UTC — trades scaled way up (user request)
- hp Kelly ceiling $25->$100 (quarter-Kelly on $10k decides actual size;
  brain/m15/oracle multipliers apply); arb $2,500->$4,000/catch (locked
  payouts, depth-limited); daytrade $10->$30; explore stays $1 (info),
  news quarantined. Cluster floor $40->$150 so concentration math scales
  with the bigger book (tests updated). Circuit breaker still 3%/day
  ($300) — 3 max-size losers trip it by design. VaR will rescale; watch
  next review. 85/85 tests.

## 2026-06-12 ~14:50 UTC — professional bankroll management codified
- bankroll_manager(): risk/trade = 1% of CURRENT equity (compounds both
  ways); portfolio heat = sum of worst-case losses, capped 10% of bankroll
  (central gate in open_position — no entry may push heat past it);
  drawdown ladder -2/-4/-6% -> x0.75/0.5/0.25 sizing, restores at the
  high-water mark; brackets size by risk/stop-distance; position_risk():
  stake for holds, stop-distance for brackets, zero for locked arbs.
  BANKROLL line in brief. 90/90 tests.

## 2026-06-12 ~15:40 UTC — model v2 + deep suite + architecture docs
- ml.py v2: stochastic GBM (80% subsample) w/ EARLY STOPPING on validation
  (model picks its own size), forest OOB accuracy (free validation),
  hyperparameter variants raced in the championship (gbm-slow won: 0.0606),
  online SGD — every settle nudges the brain INSTANTLY between retrains.
- Committee v2: 5 positive-skill members [gbm-slow .23, gbm .23, forest
  .22, forest-big .21, logistic .10]. Measured model improvement since
  logistic baseline: 0.027 -> 0.061 cv_skill (2.3x on the conservative
  measure).
- NEW tests.py: 80-check deep suite (caught a real classifier bug on
  arrival: fresh momentum ramps were labeled mean_dev/fadeable instead of
  breakout — ordering fixed). Total checks now 173. NEW ARCHITECTURE.md.
- 93/93 + 80/80, restart clean, books balanced.

## 2026-06-12 07:05 — LIVE CHART (user-directed)
Shipped: /api/stream SSE endpoint (400ms account ticks, ThreadingHTTPServer
so the held connection costs nothing) + EventSource client streaming
straight into the equity line via LWC's native series.update(). Header
total/chg now move sub-second; 2s full-state poll kept as fallback with
3s auto-reconnect. Honest ceiling: marks refresh from the 1s bulk-book
sweep; true tick data = Polymarket WebSocket, parked in TRICKS until paper
results justify live-grade infra. Tests 93/93, stream verified flowing.

## 2026-06-12 07:25 — SMART-MONEY TRACKER + TAPE-SIGN FIX (user-directed)
User shared the documented profitable-bot playbook ($75K bot): (1) fresh
wallets with no history making large bets, (2) oversized wagers, (3)
political repeat-entries. We had (2) (whale_flow big prints); built (1):
_wallet_intel profiles the wallet behind every $250+ print via
data-api ?user= (100-trade window; <100 returned = whole life; 6h cache
persisted in wallet_intel.json, 3 lookups/market/min budget, 4000-wallet
cap). _is_fresh = <=25 trades AND <=7 days old — deliberately excludes
market-maker bots (live smoke test: first wallet profiled had 100 trades
in hours, correctly excluded). fresh-wallet YES-flow returned as
wf["fresh"]; smart_verdict speaks at $300+. SHADOW-FIRST per house rule:
context field, brain feature "smart", miner feature, by_smart_money
attribution bucket — zero entry power until its bucket proves out.
(3) parked in TRICKS with a testable prediction.
BUG FOUND while wiring: whale_flow ignored outcomeIndex — every BUY
counted as YES-flow, but buying the No token is selling Yes. Fixed via
_tape_sign (3 unit tests). All by_whale history before today is
sign-tainted on No-prints; judge that bucket from a 06-12 baseline.
Tests: 102/102 bot (9 new) + 80/80 deep. Bot restarted, audit balanced.
Watch: by_smart_money bucket; brain weight on "smart" after next retrain.

## 2026-06-12 08:10 — ML UPGRADE FROM GITHUB SOURCES (user-directed)
Studied three repos, implemented four techniques pure-Python in ml.py:
(1) dmlc/xgboost — Newton boosting: leaves -G/(H+lam), split gain
0.5[GL^2/(HL+l)+GR^2/(HR+l)-G^2/(H+l)]-gamma, column subsampling. ZOO
entries "xgb"/"xgb-reg". Planted problems: 100%/98% vs plain GBM 98%/96%.
(2) scikit-learn — isotonic calibration (PAV with tie pre-aggregation);
races Platt HONESTLY (fit on first 60% of holdout, judged on last 40%,
winner refit on all); gated at holdout>=40 so it can't win by memorizing.
(3) online-ml/river — Page-Hinkley drift detector on the brain's own
per-settle logloss stream; on fire, brain_train re-anchors on post-drift
data only (>=40 labels) instead of averaging two regimes. journal DRIFT.
(4) AdaGrad per-feature step sizes for settle-time online SGD (g2
persisted in brain.json) — rare features (smart_agree) learn fast,
frequent ones stop oscillating. Both settle call sites deduped into
brain_online_learn().
REAL-DATA VERDICT (n=146, full retrain 8.9s): champion forest cv_skill
0.0747 (prev champion reading 0.0606); xgb 0.0597 + xgb-reg 0.0526 both
positive-OOS -> joined the stack (~28% combined weight); 7-member
committee. Calibration race: platt (holdout 29 < 40 gate; isotonic
eligible ~200 settles).
Tests: ml self-test 25 PASS (4 new planted problems), bot 107/107
(5 new), deep 80/80. Restarted, audit balanced.
Lines: bot.py 4867 + ml.py 700 + tests.py 281 + dashboard.html 965 +
config 116 = 6929 written code; 7708 with docs/journals.

## 2026-06-12 ~12:40 UTC — ML library + model-zoo championship
- NEW ml.py (391 lines, pure Python, self-testing): gradient-boosted
  depth-2 trees, random forest w/ feature subsampling, 1-hidden-layer MLP
  w/ manual backprop, Platt calibration, permutation importance,
  calibration tables. Library proves itself on planted ring/XOR problems
  (GBM 99%/95%, forest 95%/98%, MLP 100%/98%) before touching money.
- BRAIN 4.0: every retrain races logistic vs GBM vs forest vs MLP on the
  same chronological CV folds. FIRST CHAMPIONSHIP: random forest WINS,
  cv_skill 0.0582 (2.3x logistic's 0.0257); MLP -0.59 correctly rejected
  as overfit at n=143. Voice rose to 73% on earned skill. Retrains skip
  when no new settles (cache by n) — championship costs 5.5s.
- Sim-prior Thompson live: explorer warm-started by replay cell beliefs
  (discounted 4x). 83/83 tests.

## 2026-06-12 07:30 — RAM UPGRADE + RESTART CORRECTION (user-directed)
User authorized up to ~44GB more RAM. Spent it where the bot was throwing
data away, not for show:
(1) PRICE_MEM in-RAM tick store — packed arrays (12B/tick vs ~120B for
Python lists). EVERY price the process sees is remembered at full
resolution: 1s position sweeps + 15s watchlist (fetch_books_bulk),
single book_stats reads, and the recorder's 400-market/min scans (keyed
m:<id>). Config: memory.price_mem_hours=168 (7 days), max 3000 markets,
LRU-evict stalest. Ceiling ~20GB, expected steady state 1-3GB — leaves
plenty for Claude + browser per user constraint.
(2) chart_features now reads OUR OWN 1s ticks when coverage >=83% of the
window (downsampled to ~72 pts so the classifier sees the same shape) —
better resolution than the API's 5-min candles AND zero API calls.
(3) Monte Carlo VaR sims 4000 -> 20000 (measured 0.1s; tail now 1000
samples, CVaR95 stops wobbling).
(4) Telemetry: /api/health rss_mb + price_mem; brief MEM line (fetched
from the live daemon, since brief runs in its own process).
CORRECTION (important): today's earlier "restarts" used pkill -f "bot.py
run" — but the daemon runs as "bot.py paper" (caffeinate). Nothing was
killed; "bot.py run" isn't a command, so the nohup exited instantly. The
smart-money tracker and ML v3 were tested-but-NOT-LIVE until now (~07:30
UTC). Also killed a runaway "bot.py replay" (99% CPU since 10:40PM).
Correct restart: pkill -f "bot.py paper"; nohup caffeinate -i python3
bot.py paper >> bot.log 2>&1 &
Verified live: health shows rss_mb=149, price_mem 872 mkts in first
minute; SSE streaming; dashboard 200. Tests 111/111 bot (4 new mem
checks) + 80/80 deep.

## 2026-06-12 07:45 — DEEP REVIEW (149 settled, eff 78)
Book: 9973.72 (-0.26%), real -29.55, 105 settles/24h. Explorer VERDICT:
m15-gate hypothesis KEEP/EXCEEDED — last-30 avg +0.0167 vs -0.085 baseline,
FLIPPED POSITIVE (0 stop-outs in window). m15 split widens: disliked
-0.066 (n=24) vs liked +0.05 (n=9). Research (corrected instrument):
6-24h favorites 90-96c now 100% win, +3.9 to +11.1%/$1 (n=5-15/band) —
promotion pipeline stays the judge. Postmortem: news losses all old-era
(Sports, already blocked). NEW loser pattern: daytrade stopped SpaceX IPO
-6.09 (orderly bleed, brain-upsized — by_brain_adj upsized bucket is just
this 1 trade, ignore n=1) then RE-ENTERED same market 30min later (-1.17
open). Whipsaw watch: 7/8 24h stop-outs recovered above entry, but each
cost <0.15 (immaterial individually) — watching, not acting.
ONE CHANGE: daytrade re-entry cooldown 30min -> 6h (config
daytrade.reentry_cooldown_s, default 21600). A stopped fade is evidence
the move was repricing, not overreacting. Tests 111/111, restarted
(verified via rss_mb field present), audit balanced.
Next review watches: no daytrade same-market re-entry <6h; explorer stays
positive; by_smart_money first entries; whale baseline post sign-fix;
first hp TRUE resolution (still 0 in 28 settles — insurance exits only).

## 2026-06-12 07:50 — NIGHT PREP: WARM-START + BIGGER SIMS (user-directed)
(1) mem_warmstart(): startup thread loads the recorder's ENTIRE disk
history into tick memory — verified 2,029,688 points / 8,167 markets
loaded (~25MB packed; cap raised to 10,000 markets). Chartist and any
future consumer wake up knowing a month of every market's path instead
of starting blind after each restart. (2) MC VaR sims 20k -> 50k. (3)
Overnight replay launched (nice -15) to regenerate stale sim cells (18
cells from Jun 11) with today's code — Thompson priors refresh by
morning. Final sweep: health ok, audit balanced, SSE streaming,
dashboard 200, display assertion active (12h), bot 4.7% CPU / replay
nice'd 0.9%. Tests 111/111 before restart.

## 2026-06-12 08:00 — 15GB BUDGET + M15 MILESTONE (user-directed)
User committed 15GB RAM. Built three real consumers (no padding):
(1) BOOK_MEM — top-5 depth ladders both sides, every book fetch, packed
88B/tick, 14-day cap. The TRICKS #5 dataset (whale-print depth deltas)
now accumulates ~86k snapshots/day/position-token. book_series() ready
for the correlation test once days of data exist.
(2) CORPUS — recorder's full row set hot in RAM (1.9M rows, ~400MB),
appended live; m15 retrains skip the 2M-row CSV re-parse.
(3) PRICE_MEM extended: 30-day ticks, 10k markets, warmstart rewritten
to mem_preload (splices history BENEATH live ticks — fixes coverage gap
where early-ticked markets lost their history) + 48h of 1-min token
candles for every open position.
MILESTONE: m15 retrained on the hot corpus (4,128 labeled obs) now
BEATS THE MARKET OOS — Brier 0.19433 vs market 0.20145, skill +0.0071,
n_holdout=1032. First model to clear the hardest baseline. Promotion
decision belongs to the daytime deep review per pipeline rules.
RSS 952MB and climbing toward budget as real data accrues (~1.5-2GB/day
from depth ladders). Tests 114/114. Restarted, audit balanced.
NIGHT SCHEDULE: recurring reviews DELETED per user; one-shot 4:57AM
final check scheduled (verifies replay finished, judges overnight,
recreates daytime crons). Overnight replay still running nice'd.

## 2026-06-12 08:20 — CONTINUED TESTING (user-directed)
TRICKS #1 resolution-source edge: REJECT, REVERSED (530 labeled
favorites, gates applied, one obs/market): objective-feed 97.0% win
+0.13%/$1 vs judgment+sports 97.5% +1.92%/$1. REAL FINDING: sports
favorites 75/75 across 61 families +4.72%/$1 (Wilson LB ~95.2% vs 95.5%
breakeven — one good week short of conclusive); crypto-threshold
NEGATIVE -0.69%/$1 (299 obs = only 24 families). H#5 time-of-day
within-explorer: no actionable signal (18h -0.21 but n=13). Closed.
ONE CHANGE: band/category learning blocks now age out on a 14-day
window (miner precedent) — a block earned by a dead failure mode
(stop-churn) no longer governs forever. Explorer's Sports block expires
~06-24; sports favorites hypothesis gets its natural live test then.
Tests 114/114, restarted, audit balanced. Watch: crypto-threshold hp
entries (negative bucket — m15/probation should be catching these),
sports-block expiry date, m15-beats-market promotion decision (daytime).

## 2026-06-12 08:45 — MODEL 16: THE LEARNED CHARTIST (user-directed)
New ML category chartml.py for chart analysis + pattern mining:
(1) MOVE MODEL — self-supervised from tick memory: every recorded 3c+
move is a labeled event (did it revert >=1c within 30min?). 5,675 real
events mined; base revert rate 63.0% (independently confirms the lab's
fade-the-move verdict). Walk-forward championship: gbm OOS skill +0.0753
on 1,703 chronological holdout events — the largest validation set of
any model in the system; that evidence grants gate power immediately
(same standard as m15: validation data independent of our trades).
WIRED: fast fade desk now requires p_revert >= 0.5 (config
daytrade.ml_min_revert); chart_ml recorded in context; by_chartml
attribution bucket; model_acted("chartml") veto counter; retrains every
6h from tick memory, adopts only if walk-forward skill stays positive.
(2) MINER SIGNIFICANCE — vetoes now require exact binomial surprise
(p < 0.15 vs the book's own win rate, floored at 30%) on top of the
dollar thresholds: bad-luck patterns can no longer earn vetoes.
(3) chartml self-test: separates planted mean-reversion from momentum
regimes (skill 0.199), proves no-future-leak under truncation.
Tests 117/117 + chartml ALL PASS + deep 80/80. Restarted, audit
balanced, model 16 live. Parked: chart-outcome shape model (TRICKS #9).
Watch: by_chartml buckets (revert-likely vs continuation), chartml veto
count in MODELS line, 6h retrain skill trajectory in journal.

## 2026-06-12 09:05 — TRICKS #9 OVERNIGHT VERDICT: REJECT
Chart-outcome shape model: Brier 0.15469 vs market 0.14955 (skill
-0.00514, n_holdout=753 of 2,508 labeled paths). Shape predicts NOTHING
about resolutions the price doesn't already know — while the same
features predict 30-min reversions at +0.075. Horizon decides whether a
signal is information or decoration. outcome_model.json saved with
model=null (shadow rule held); no code change. Morning review: nothing
to act on here — this closes the question.

## 2026-06-12 09:20 — OVERNIGHT REPLAY COMPLETE
Sim cells regenerated at scale (18 cells, n now in the thousands).
Standout: sports-game|90c|<24h ran 1,901/1,901 in replay — independently
corroborates tonight's live-research sports finding (75/75 families) on
a third instrument. other|90c|<24h: 93.3% over 2,957 (+~3.7%/$1 at 90c).
Cells feed explorer Thompson priors at the x0.25 sim discount, as
designed. All overnight work now complete; 4:57 report remains.

## 2026-06-12 05:15 local — MORNING REPORT (5 AM check)
Account: $9,961.20 (-0.39% from $10k start; -$12.52 overnight). 180
settled (eff 96), 115 in 24h. Bot healthy all night, audit balanced,
RAM 1.16GB: 3.3M ticks / 216k depth ladders / 2.5M-row corpus.

OVERNIGHT BY STRATEGY:
- DAYTRADE was the bleeder: 9 settles, -$13.75 (avg -1.98 incl. earlier).
  Autopsy: -$11.28 of it = IN-GAME TENNIS fades (the exact failure mode
  that killed news). All losing entries PRE-DATE model 16's gate (no
  chart_ml in their context). The bot already self-halved daytrade (x0.5)
  and the Sports auto-block sits at 5 of 6 needed material settles — one
  more Sports settle triggers it.
- COOLDOWN BUG FOUND: SpaceX $2.2T was stopped 06:54 (-6.09) and
  re-entered before 11:36 (-3.90) — the 6h cooldown shipped at 07:45 is
  an IN-MEMORY dict, wiped by every restart (we restarted 4x shipping
  features). Cost overnight: ~-$4 to -8.
- Explorer: +17 settles, only -$0.21; last-30 avg +0.0087 (still
  positive, drifting from +0.0167 — watch).
- hp: +$0.15 overnight (take-profits); STILL zero true resolutions in 32
  settles. News: x0.5 + auto-tuned (down-moves blocked). Arb: 6 locked.
- m15 verdict bucket strengthens: liked +0.015/22 vs disliked -0.05/31.
- by_chartml + by_smart_money: empty — no post-gate settles yet. Model
  16's first 6h retrain due ~14:50 UTC.

EXPERIMENTS: replay KEEP (sports 90c 1,901/1,901 — 3rd independent
confirmation); TRICKS #9 REJECT (shape adds nothing at resolution
horizons, -0.005 vs market). Both logged with numbers above.

ONE CHANGE SHIPPED (config-only per night rules): price_mem_tokens
10000 -> 20000 — the market cap was HIT overnight (LRU evicting fresh
markets); budget 15GB vs 1.16GB used. Hot-reloads, no restart.

WHAT TO IMPROVE FIRST TODAY (ranked, with numbers):
1. PERSIST the REENTRY cooldown map to disk (~5 lines in bot.py): the
   6h daytrade cooldown must survive restarts. Evidence: -$3.90 repeat.
2. Judge model 16 on its first gated settles (by_chartml bucket) and the
   14:50 retrain skill in the journal; consider ml_min_revert 0.5->0.55
   only AFTER settled evidence exists.
3. Decide m15 promotion (it beats the market OOS +0.0071, n=1032):
   next graduated power = small sizing tilt on LIKES, judged by
   by_m15_verdict liked bucket (+0.015/22 now).
4. Daytrade Sports: if the 6th material settle doesn't auto-block by
   midday, consider a config category gate. In-game fades cost -$11.28
   tonight and -$31 lifetime across strategies.
5. Favorites book still has ZERO true resolutions (32 settles, all
   insurance exits) — the 96-98.9c thesis remains unproven; the 90-93c
   promotion pipeline (research +2.7..8.5%/$1, sports 1,901/1,901 sim)
   is where the real edge evidence keeps accumulating.
## 2026-06-12 mini: ALERT down->25% = explore Sao Paulo $0.98 stake at 0.72 mid — hold-for-label by design (no stop, stake=insurance); no action. Arb mids read 0 (multi-leg, settle at lock); cosmetic.
## 2026-06-12 mini: repeat alert = same designed explore hold; FIXED mini_review.sh bleeder alarm — now excludes explore/arb and needs pnl < -$1 (real bleeders still alert; $0.98 stakes and locked arbs don't).

## 2026-06-12 15:15 — DEEP REVIEW (187 settled, eff 101)
Wins since 5am: (1) FIRST hp TRUE RESOLUTION (+0.04, 98c won; hp book
now +0.20 overall, first positive reading ever). (2) Daytrade Sports
AUTO-BLOCK fired (6th material settle) — in-game tennis ban now learned,
not imposed. (3) Model 16 retrained ITSELF at 14:36: skill 0.0753 ->
0.0994 on 6,955 events, champion flipped gbm -> xgb (the Newton booster
won its first championship). (4) m15 gate effect visible: disliked
bucket avg shrank -0.066 -> -0.009 (gated entries stopped settling);
liked +0.018/25. (5) whale post-sign-fix: agree +0.157/10 vs disagree
+0.05/13 — right direction, small n.
Watch items: by_chartml/by_smart_money still empty (no qualifying
settles). Whipsaw count 7/8 again — still concentrated in daytrade
(x0.5 + Sports-blocked + gate pending); holding.
H#3 spread->news: REJECT (wide lost LESS, -0.623/9 vs -0.803/15).
NEW H#1b: crypto-threshold favorites negative bucket (research -0.69%/$1
n=299/24fams; live Crypto +2.11 -> -1.66 today; hp holds $137 BTC No).
ONE CHANGE: REENTRY cooldown now PERSISTED (reentry.json, 24h prune,
health field "reentry") — restarts can no longer wipe the 6h daytrade
ban (documented cost -$3.90). Tests 117/117; restart verified via new
field. Research: 6-24h favorites 90-96c still 100% win +3.8..11.1%/$1.
Next review: first by_chartml settles, crypto-threshold bucket, m15
liked bucket at n>=30 for promotion call, explorer last-30.

## 2026-06-12 16:05 — "MAKE IT WIN": THE REALLOCATION (user-directed)
Diagnosis: bleeding already stopped (news+daytrade = -$37 of -$42, both
self-quarantined). But the kelly book was structurally fishing the dead
pool: confined to 96-98.9c at 24-48h (research: ~flat to NEGATIVE — 97c
-3.7%, 98c -6.09%/$1) and BARRED from 90-95.9c <24h where THREE
instruments agree the edge lives (research +3.8..11.1%/$1 across 2 runs;
replay sports 1,901/1,901; live explorer resolutions).
SHIPPED (3 coordinated changes):
(1) LANE90 OPEN — hp may now buy 90-95.9c favorites 0.5-24h out, at
HALF size (size_factor 0.5) until live settled money confirms; every
gate still applies (m15, models 1-12, kelly, heat, category caps).
by_lane attribution bucket judges it. KILL CRITERIA: r90 bucket
negative over 15+ effective settles -> lane closes.
(2) CRYPTO-THRESHOLD GATE — hp skips 'BTC above $X' style markets
(research -0.69%/$1 n=299; live bled -4.50 peak today on $137 exposure).
Up-or-down crypto (tested +5.7%/$1) stays. Explorer's $1 probes keep
gathering H#1b evidence.
(3) by_lane bucket + 3 new tests (crypto-threshold detection).
Tests 120/120, restarted, audit balanced. Expectation set honestly:
research says the lane is worth roughly +4-8c per $1 deployed; at
half-size kelly entries this is dollars-per-day, not riches — but it is
the first time the bot's REAL money points at its STRONGEST evidence.
Watch: first r90 entries + their by_lane settles; crypto-threshold
absence from new hp entries; heat as the lane fills.
## 2026-06-12 16:25 — HOTFIX: fetch_books_bulk crashed on EMPTY books since the memory feed shipped (None not subscriptable -> whole 100-token batch lost per occurrence; 1s monitor partially blind). Guard added; tests 120/120; restarted. Found by lane90 entry watcher.

## 2026-06-12 17:55 — DEEP REVIEW (197 settled, eff 106): LANE90 DEPLOYMENT DEBUGGED
State: hp climbing (+0.46, second band win 96c +0.22); m15 promotion
threshold MET (liked n=33 avg +0.031 vs disliked -0.001/35 — promotion
decision deferred one review to keep this one focused on the lane);
whale agree +0.131/15 vs disagree +0.052/16; explorer -0.04 lifetime avg;
no new stop-outs in 2h (whipsaw stat stale, holding).
INVESTIGATION (the review's test, replacing a backlog item): why zero
lane90 entries? Funnel probe: 161 in-band <24h, 21 passed volume, first
sampled candidate PASSED all book gates — yet no entries. THREE stacked
blockers found and fixed:
(1) KELLY REFUSED UNPROVEN BANDS — bands 90-95 had no history; pooled
fallback Wilson << breakeven -> $0 sizing forever. Fix: band_win_stats
now seeds from the corrected research instrument (full counts; Wilson IS
the discount — double-discounting is what zeroed the lane). Math now
funds 90-93c, refuses thin bands: matches the research verdict by
construction.
(2) QUERY STARVATION — 1,000+ markets end inside 24h; the single
ascending-endDate query never reached classic 24-48h candidates after
the lane widened the window. Fix: two query windows, own page budget.
(3) ALLOCATION FULL — hp book sat at $4,000.17/$4,000; lane entries had
$0 room. Fix: +$250 from the quarantined news book's idle allocation
(news x0.5, 0 open, tuned-against; keeps $250 for its gated probes).
Also fixed: my own test asserting 95c must stay unfunded — wrong; live
band history legitimately funds it now. Replaced with the property test
(kelly refuses when Wilson < breakeven). Tests 123/123. Restarted twice,
verified via reentry field; audit balanced.
Next review: FIRST r90 entries (watch by_lane), m15 PROMOTION DECISION
(threshold met), crypto-threshold absence from new hp entries.

## 2026-06-12 18:20 — LANE90 IS LIVE (addendum to 17:55 review)
Correction to blocker (3): allocation was NOT full — strategy_budget =
alloc + realized - cost ($3,967 free); the brief's v4000 is display
value. The +$250 move was harmless but unnecessary; my diagnosis was
wrong and the offline dry-run proved it: kelly funded $587 at 92c yet
the REAL scanner returned 0. TRUE final blocker: a THIRD band check —
the VWAP fill-price gate still used the classic 0.96 floor, killing
every lane candidate at the last step. Fixed (band_ok), tests 123/123.
DRY-RUN: 3 r90 opportunities found. LIVE after restart: 4 r90 positions
open — $23.00 @92c x3 (sports O/U, 1.3-1.6h to resolution) + $5.74
@95.7c. First by_lane settles land within ~2 hours. Watch them like a
hawk at next review; kill criteria unchanged.
LESSON (for the log, honestly): one band concept was enforced in THREE
places; widening it required finding all three. The dry-run-the-real-
scanner instrument (scan_high_prob offline with empty skip set) found
in minutes what funnel re-implementations missed for two hours — test
the actual code path, not a copy of it.

## 2026-06-12 19:05 — SMART NEWS (user-directed: "trade news too just get smarter")
News 2.0 ships as a SPLIT, not a revival of the dead trade:
- CONFIRMED moves (matching headline + sentiment agreeing with direction,
  |sent| >= 0.3) now FOLLOW the move — information drifts. Model 16 holds
  a veto: if the learned chartist says this move shape historically
  REVERTS (p_rev >= 0.5), the trade abstains. mode="follow-news" in
  context; chart_ml recorded; judged via by_news_backed + mode buckets.
- UNCONFIRMED moves: unchanged quarantined fade (x0.5, Sports blocked,
  down-moves blocked, 6h-ttr gate). The in-game killer stays dead — the
  ttr gate applies to BOTH paths.
- Daytrade's reuse of this scanner explicitly excluded from follow-mode
  (it is a pure fade desk with a fade-shaped chart gate).
What's different from the news book that lost $17.65: the old one
treated EVERY big move as news; the new one requires the news to
actually exist, agree in direction, and pass a model that beats base
rate by +0.099 OOS at predicting which moves run. Learning multiplier
stays x0.5 until it earns its way back. Tests 126/126, restarted, audit
balanced. Watch: first follow-news entries, their by_news_backed bucket,
news multiplier trajectory.

## 2026-06-12 19:40 — LOSS AUTOPSY + ORACLE COVERAGE FIX (user: "focus on why trades are losing")
THE AUTOPSY, mechanism by mechanism, on settled money:
(1) -$23.69 of the last -$27 = pre-fix daytrade stops. Already dead:
ZERO daytrade entries since the gate; post-gate daytrade is +0.43/2.
(2) hp stops TESTED against resolutions: both lifetime stop-outs would
have lost 6.5x MORE held to the end (-0.60 stopped vs -3.93 held). The
stops are VINDICATED — kept. (The 7/8 whipsaw stat was other books.)
(3) Post-fix era (24 settles since 13:00): +$0.03 total. hp +1.71/5,
daytrade +0.43/2, news -0.96/1 (pre-smart-news), explore -1.15/16.
(4) THE LIVE FINDING: explorer's two big losers were weather favorites
m15 LIKED (0.969/0.971) — and the weather ORACLE never spoke: 2 oracle
reads across 69 weather settles. Root cause: the parser knew ONE
question shape; the real book is 52/62 exact-pin ("be 17°C on June 12")
+ 6/62 Fahrenheit ranges. The fundamental-data oracle for the bot's
LARGEST trade family was 94% blind.
FIXED: _wx_parse handles all three shapes (directional / exact-pin /
F-and-C ranges), min-forecast for "lowest temperature" questions,
margin = degrees-from-pin so the existing >=2.0-degree sizing bonus
applies naturally. Coverage 62/62 real questions (was 4/62). Live
end-to-end read verified (Amsterdam pin: forecast 17.4 -> oracle says
the No bet loses, margin 0.4 — exactly the read that was missing).
Tests 131/131 (5 new shapes + stub fix). Restarted, audit balanced.
Watch: by_oracle bucket should now FILL on weather entries; oracle
disagree-then-loss cases become the evidence for a future oracle gate
(shadow rule: attribution decides whether it earns a veto).

## 2026-06-12 18:00 — PRE-DEPARTURE: DEPLOY MORE + CHART FIX + WATCHDOG
(user leaving for the day)
(1) DEPLOY MORE within the rules: arb reserved $4,000 but has only ever
found ~$220 of locked arbs — $1,000 of idle reserve moved to the proven
favorites book (hp allocation 5,250). hp per-category cap 12 -> 16
(weather, the validated family, was bumping it). Per-trade risk still 1%
equity; heat cap still 10%; lane90 still half-size. Deployment grows as
candidates pass gates — the levers that matter today: lane90 live,
oracle now reads 62/62 weather questions (more x1.25 bonuses), crypto-
threshold money freed.
(2) CHART "ALL" FIXED: LWC's default minBarSpacing (0.5px) physically
cannot fit 8,400 history points in the pane, so fitContent silently
clipped to ~2h. All three charts now minBarSpacing 0.001 + ALL sets the
explicit full span (first equity point -> last). Full 40.6h visible.
(3) WATCHDOG REPLACED: the Jun-10 watchdog (running since Wed) restarted
on a SINGLE failed check via osascript — explains the pile of Terminal
tabs; could race deliberate restarts. New one: process-existence first
(never races), 4-check (~2min) hung tolerance, nohup restarts, log
rotation. Old killed, new verified running.
SWEEP: tests 131/131, health ok, audit balanced, reentry map persisted
(11 cooldowns), SSE streaming, dashboard 200, 1 bot + 1 watchdog,
system-sleep prevented via bot's caffeinate; display assertion expires
~13:00 local (screen may sleep; TRADING CONTINUES — system stays awake).
Reviews continue: mini 5min + deep 2h crons active in this session.
## 2026-06-12 18:25 — user: '$706 invested is not much' — correct: the 10% heat cap WAS the ceiling (full-stake holds). Raised: heat 15%, risk/trade 1.25%, hp cap $150. Drawdown ladder + 3% circuit breaker unchanged — they shrink it all automatically if losses come.

## 2026-06-12 18:35 — THROUGHPUT PACKAGE (user: "10x the trades")
Levers shipped: scan cadence 2min -> 1min (FIXED: interval was read once
at startup, now hot-reloads — real bug); explore band 85-99c -> 75-99c;
explore horizon 1d -> 2d; explore min window 1h -> ~2min; hp slots 40 ->
60. Watchdog proven in anger: pkill'd bot restarted by it in ~2.5min.
HONEST LIMITS, measured: sub-15min "fast settle" lane is SUPPLY-limited
— dry-run found 0 band-passing candidates right now (late crypto
binaries pin >0.99 where probes teach nothing; mid-window they're coin
flips with no favorites thesis). Refusing to buy noise to hit a count.
Current entry rate: 11/hr vs ~5/hr baseline (~2x), expected to climb
through US evening (sports + crypto hourlies enter the wider band/
window). 10x literally = market supply dependent; the bot now takes
everything that qualifies, every minute.
Earlier this hour (user requests): heat cap 10->15%, risk/trade 1->
1.25%, hp per-trade $150 — invested rose $706 -> $735 and grows as
candidates pass. Watch: entries/hr at next review, first sub-1h probes
when supply appears, heat under the new ceiling.
## 2026-06-12 18:55 — user: 'trade 3k at a time' — granted where riskless (arb $3k/trade), scaled where risky: risk/trade 2%, hp $300 base (~$470 with stacked bonuses), heat 20%. $3k on one binary = 30%/trade = ruin math + self-tripping circuit breaker; refused with explanation. Ladder + breaker unchanged.
## 2026-06-12 19:15 mini: FIRST r90 settles — Derry O/U 92c gapped to 11c in-game, -20.25 (stop = placebo on jump markets, the news lesson in the kelly book); second r90 model-exited -0.72. One 8%-tail loss on trade #1 ≠ lane broken (kill rule: 15+ eff settles), but 19:41 deep review MUST evaluate: exclude in-game/live sports from lane90 (pregame only), since research 75/75 can't distinguish pregame vs live entries. realized -63.15.

## 2026-06-12 19:41 — DEEP REVIEW (210 settled, eff 110): THE SPORTS LESSON, PAID FOR
The damage: lane90 sports 0/5, -$50.56 (Derry O/U -20.25; Canada-Bosnia
exact scores/totals stacked FIVE correlated entries on one live match).
All in-game gap-deaths — postmortem: "avoid the entry, no exit can save
it". Live 0/5 contradicts research 75/75 (P ~ 3e-8 under 97% win): the
instrument is WRONG for sports; suspects logged as H#9 (one-obs-per-
market hides intra-game 90c re-crossings). House rule applied: live
settled money outranks every instrument.
THE STAR: m15 STRONG-DISLIKED ALL FIVE losers (0.705-0.866 vs 0.92-0.955
entries) while its graduated power only halved size. Disliked bucket
-1.22/42 vs liked +0.04/39.
SHIPPED (one coordinated fix for this loss event):
(1) m15 PROMOTED — strong dislike (p < entry-5c) now SKIPS the kelly
book, not just explore. Would have prevented the entire -$50.56.
(2) lane90 excludes sports-game cluster until H#9 is explained.
(3) One lane entry per market family (scan-local + open positions via
held_names) — no more five-bets-one-match correlation bombs.
Bot self-responses already in flight: hp learning x0.5; bands 92/95/96
accumulating negative material evidence. whale agree bucket poisoned by
the sports cluster (-2.01/24, in-game tape always "agrees" with the
doomed favorite) — judge whale from a post-06-12 baseline AGAIN; in-game
contamination is why. Tests 131/131, restarted, audit balanced.
Account 9912 (-0.88%). The -$50 bought: m15's promotion case proven
beyond argument, a flawed instrument caught, and correlation capping.
Next review: lane90 non-sports performance, m15-skip counter (model
m15 in MODELS line), explorer last-30, H#9 crossing-count test.

## 2026-06-12 20:10 — SESSION HANDOFF (user usage running out)
Bot + watchdog are INDEPENDENT processes — trading, learning, retraining
(brain each settle, m15+chartml every 6h, evolver) all continue without
Claude. What DIES with this session: the 5-min mini and 2-h deep review
crons (recreate by asking Claude: "recreate the reviews").
State at handoff: $9,912 (-0.88%), 210 settled, heat ladder armed,
m15 promoted (strong dislike skips both books), lane90 non-sports only +
1-per-family, smart news live, oracle 62/62 coverage, sizing: 2%/trade,
heat 20%, hp $300 (arb $3k).
UNFINISHED: the ultracode audit+science workflow was killed mid-run.
Re-run next session with: Workflow({scriptPath: "~/.claude/projects/
-Users-you/075e8284-debf-4382-83d8-5b8ed62978bc/workflows/scripts/
smarter-bot-audit-and-science-wf_ea4b2133-0d0.js"}) — it audits today's
5 changed subsystems for bugs and runs the H#9 experiment (why research
said sports 75/75 but live went 0/5) + the kelly-seed honesty check.
Until H#9 is answered, lane90 stays non-sports.

## 2026-06-12 20:35 — ULTRACODE FLEET: 19 VERIFIED BUGS FIXED + H#9 CLOSED
35 agents (5 auditors + adversarial verifiers + 2 experimenters +
checkers), 1.76M tokens, 1 finding rejected, 19 confirmed — ALL FIXED:
THE BIG ONES:
- is_in_game(): every time gate measured hours-to-endDate, but sports
  endDate = TOURNAMENT end — live matches sailed through all 6h gates
  all day. gameStartTime is the honest clock. Wired into hp (both
  lanes), news (gate also fail-closed now), follow-news.
- Model-9 band exit floor (0.90) sat ABOVE r90 entries — every lane
  entry below 92c churned instantly; part of the 0/5 was SELF-inflicted.
  r90 now HOLD-TO-RESOLUTION (exempt models 9+12, stop 0.02/target
  0.995) per the H#9 science: held = 97.0% (n=235, LB 95.7%) vs 64%
  with band exits.
- Oracle: 32 directional questions misparsed as pins (F/lowest missing
  from _WX_RX) — the x1.25 bonus was firing on guaranteed losers;
  margins now side-signed degrees + kind-aware bonus (wx>=2.0deg,
  crypto>=0.15p); oracle_v=2 labels so attribution never mixes eras.
- Kelly seed now FAMILY-LEVEL (union table, tradable universe only):
  raw seeding pseudo-replicated 299 crypto obs = 11 families. Funding
  now: 93c $974, 92c $133, 94c $154, 90c REFUSED — sized by honest
  Wilson, not inflated n.
- Circuit breaker: realized-today only + enforced in open_position
  (was main-scan-only; daytrade thread kept trading after it fired).
- m16 train/serve skew: inference now resamples to the 30s training
  cadence; chart_features resamples by TIME not index; _best_series
  picks by quality (the `or` short-circuit silently disabled the veto).
- Lane caps by EVENT id (family_of saw 6 "families" in one match);
  sports lane exclusion now phrasing+cluster+gameStartTime, PERMANENT
  (H#9: the 1-min recorder physically cannot see in-game gap risk —
  Derry sat pinned 158 snapshots then gapped between two of them).
- Probation never flags lane entries; risk_per_trade_pct knob now
  actually wired to the favorites book; boosted sizes re-validate
  against book depth (+56% phantom shares fixed); arb honors the
  learning pause; memory stores locked (eviction races aborted scan
  passes); CORPUS trimmed to 14d (budget was decorative).
H#9 CLOSED: 75/75-vs-0/5 = resolution-lag censoring (all 5 losers STILL
unlabeled; 43% of 06-12 sports episodes censored) + hold-vs-stop label
mismatch. Whipsaw was a minor non-sports effect (first-touch 91.6% in
multi-cross markets — below breakeven; avoid re-cross entries).
DEFERRED (1): HEADLINES pubDate freshness (medium) — logged, next review.
Tests: bot 134/134, deep 80/80, chartml+ml ALL PASS. Restarted, audit
balanced. Watch: r90 hold-to-resolution settles, by_oracle v2 bucket,
daytrade slow-path chartml counter, breaker behavior on next red day.

## 2026-06-12 14:4x — Era hygiene + headline pubDate (the "still losing" diagnosis)
- Diagnosis: last-3h realized −$60.99 was ENTIRELY the pre-fix sports-r90 cohort settling
  (Derry −20.25, 4x Canada–Bosnia exact scores, spreads, O/Us — all entered before the
  06-12 sports exclusion; resolution-lag censoring from H#9 landing as predicted).
  Only $27.90 of that cohort remains open.
- high_prob living record (excl. dead cohort): +$1.40 over 9 material settles, last8 +$1.63.
  The pause was punishing a strategy that structurally no longer exists.
- SHIPPED 1: dead_cohort() era hygiene in compute_learning — sports-r90 settles excluded
  from streak/band judgment, wins and losses alike; displayed totals stay unfiltered.
  Result: high_prob mult 0.0→1.0; news/daytrade correctly stay at 0.5 (living losses).
- SHIPPED 2: _rss_items() — headlines now stamped with real pubDate (naive "-0000" treated
  as UTC), items >6h old dropped at the door; HN uses created_at_i. Kills the
  restart-resurrects-stale-news-as-breaking bug behind bad news confirms.
- Tests: bot 138/138 (4 new), tests.py 80/80. Restarted; health ok, audit balanced.

## 2026-06-12 ~16:20 PT — Deep review: SHIP NOTHING (evidence bar not met)
- Since last entry: 5 settles, 5 green (+$0.35). high_prob 2/2 (+0.04 incl. the
  manually-closed Paraguay holdover at breakeven), explore 3/3 (+0.31).
- by_oracle v2: now 4 settles, 4 wins, +$0.97. Promising, n far too small to act.
- by_lane r90: no new entries since sports ban (correct); no pending lane risk.
- by_chartml / by_smart_money: zero tagged settles yet — gates active but no
  qualifying entries have resolved. Watch.
- H#1 (weather favorites differ from backtest): hp material living-era weather
  n=6 (33% win, +$0.25 — band exits truncate losers); needs 20+. INSUFFICIENT,
  keep collecting. ALL-weather (any strategy) n=54, 43% win, −$2.25 — mostly $1
  explore probes, not evidence about hp sizing.
- Health: ok, audit balanced, reentry 42, rss 1.7GB, no tracebacks in last 200
  log lines (HTTP client-disconnect noise excluded by check).
- Era-hygiene reminder applied throughout: dead sports-lane cohort excluded.
- DECISION: no change ships. Post-fix cohort is 100% green but tiny; the right
  move is volume, not knobs.

## 2026-06-12 ~17:25 PT — ML-LAB #1: SHIP NOTHING (all models healthy, no gate has new OOS evidence)
- (1) Drift: skill_history n=199; brain skill +0.076..+0.086 (stable), voice 0.80-0.84,
  m15 shadow skill_vs_market -0.00295 (flat, unchanged). No Page-Hinkley alarms in
  models_state; m1 calm/1.0, m2 at-avg/1.0. No drift action.
- (2) m15 OOS on the bot's own living settles (era-hygiene applied): logloss edge
  vs market price +0.1427, n=95. Caveat stated: selected-trade sample with
  exit-truncated losses — supports keeping the m15 veto gate, not promotion to
  pricer. Note the contrast: shadow skill on ALL markets is ~0 — m15 helps most
  exactly where the bot trades.
- (3) chartml move model: champion xgb, holdout skill 0.0994, n_events 6,955 —
  unchanged since last retrain; nothing to re-fit yet (events accrue with tape).
- (4) Buckets: by_oracle v2 4/4 +$0.97 (n too small to upsize); by_lane r90 no
  post-ban entries; by_chartml & by_smart_money zero tagged settles. No gate
  promotion/demotion — nothing reaches evidence bar.
- (5) Kelly seed sanity: family-level union seed intact (gen 20:35 UTC). Bands
  93/94/99 at 100% eff-rate (eff_n 16/20/62) fund; 90c at 94.3% over eff_n=20
  still below the ~95%+ needed at its price -> correctly refused. 138/138 tests
  pass (incl. 'funds 93 / refuses 90' property test).
- DECISION: zero changes. Models earn their keep; gates judged only on OOS.

## 2026-06-12 ~17:51 PT — Deep review: SHIP NOTHING (zero settles in window)
- Window since ML-LAB entry is ~25 min: 0 new settles. Post-fix cohort remains
  10 settles, 8 wins, +$0.36. Health ok, audit balanced, scans every 2-3 min,
  reentry 51 persisted.
- Deployment: arb $198 + hp $191 + explore $6 = ~$395 of $9.9k. hp supply-thin
  overnight (96-98.9c, 24h+ window); model 4 18-24 UTC block just lifted.
- by_lane r90: ZERO entries since the sports ban. Could be honest supply
  drought (needs non-sports 90-95.9c, 0.5-24h, kelly-funded band) — WATCH ITEM:
  if still zero by tomorrow's reviews, dry-run scan_high_prob offline with an
  empty skip set to prove the funnel passes a synthetic candidate (lesson from
  the triple-blocker incident: never assume, dry-run the real scanner).
- H#4 (small-edge arbs realize full profit): n=2 of 5 needed — both realized
  at/above locked profit (+0.50, +0.27). INSUFFICIENT, tracking positive.
- DECISION: no change ships. No restart, so test suites not re-run (no code
  or config touched).

## 2026-06-12 ~18:45 PT — ULTRACODE FLEET #2 (44 agents): era hygiene completed across ALL learners
- Fleet: 4 parallel audits + 2 adversarial verifiers per finding. 15 survivors, 5 killed.
- THE finding (triple-confirmed, conf 0.97): five learners still trained on the dead
  sports cohort. SHIPPED one principle at five sites — dead_cohort() filter threaded into:
  m4 time_of_day_model (bot.py ~1695), band_win_stats kelly (~1168), mine_patterns m11
  (~3125), bayes_confidence m3 (~1681), brain_train m13 (~1956).
- Evidence highlights: m4's 18-24h hp block was 100% artifact (all-settles -$59.22 vs
  living-era +$0.89/12 — would have needed ~800 settles to self-heal while suppressing
  entries 6h/day); kelly 96c flipped $0 -> $418; lane90 watch item RESOLVED: funnel was
  BROKEN at the miner-veto + kelly gates, not in drought (a real candidate died there today).
- Verified live after restart: m4 blocked.high_prob=[] (news 00-06h block correctly
  remains — living-era losses); m3 hp p_win 0.48->0.59 n=42 mult 1.0; bands now fund
  92c $133 / 93c $974 / 94c $154 / 95c $41 / 96c $418. Health ok, audit balanced,
  reentry 54. Suites: bot 143/143 (5 new era-hygiene property tests), tests.py 80/80,
  chartml PASS, ml PASS.
- DEFERRED (logged, not shipped tonight): (a) main-loop ACCOUNT_LOCK gap (conf 0.7,
  real but touching lock discipline at night is riskier than the torn-save it prevents
  — next deep review with full test plan); (b) scanner page-budget truncation — push
  volume filter server-side IF gamma supports volume_num_min (verify first); (c) fast-
  settle crypto lane: killed by verifiers — no instrumented evidence yet, needs a
  research bucket before any gate opens.

## 2026-06-12 ~19:35 PT — SPORTS-DESK v1 shipped (user-directed sports re-entry, probation rails)
- User override of the categorical sports ban: "keep betting on sports... fine tune the
  ways you bet." Designed + shipped a PROBATION probe instead of repeating the -$94 style.
- Rails: PRE-GAME ONLY (is_in_game absolute — the in-game style stays dead); $5/trade;
  $50 budget = DAILY REALIZED-LOSS ceiling (open risk + today's settled probe losses;
  stopped losers cannot free budget — fix from adversarial risk review, which caught the
  open-exposure-only version allowing ~6x burn overnight); riskless arb baskets excluded
  from the budget; one bet per EVENT within AND across scans (held event_ids seeded into
  dedup); probes pass every other gate (m15 veto, bands, depth, breaker); every entry
  tagged context.sports_probe=1 -> judged as its own cohort. lane90 stays sports-free.
- Promotion path: 15+ material probe settles net positive authorizes the deep review to
  raise caps; negative record and the probe dies on its own evidence.
- Review: 2 hostile agents pre-ship; correctness PASS, risk REFUTED v1 (realized-loss
  hole) -> fixed -> rails now locked by 6 new property tests. Suites: bot 148/148,
  tests.py 80/80. Restarted: health ok, audit balanced, probe live, room $50.00.

## 2026-06-13 ~02:35 UTC — SPORTSEDGE shadow instrument shipped (user-directed "sports model")
- User asked for a self-learning sports model that finds exploits for pre-game + live.
  Ran a 9-agent recon+design fleet (wf_3212507c-110) FIRST to measure reality before
  building (the H#9 lesson). Verdict: BUILD SHADOW-ONLY, ZERO SIZING.
- Measured reality (recon): pre-game sports prices are near-EFFICIENT — closing-line
  Brier collapses 0.064->0.0001 (n=723); pre-game ECE 0.07, residuals noise. The only
  defensible edge is LIVE cross-source latency (ESPN scores before the thin PM book
  reprices) but it is UNMEASURED (scores_stats latencies=[] over 34 events) and the
  at-jump spread (~28c mean) likely eats it. So: a model that BETS pre-game has ~no edge
  to harvest; the honest product is a self-grading instrument that earns sizing via
  measured CLV, never backtest.
- SHIPPED: sportsedge.py (pure-python, 10/10 self-tests) — Elo fair value, isotonic
  calibration (ml.fit_isotonic), CLV, Brier/ECE scorecard, Page-Hinkley drift, latency
  instrument, whitelist (same-day single-game moneyline in SCORE_LEAGUES; rejects
  futures/props/wrong-day/non-league/same-city). edge_verdict DEFAULTS bet=False/size=0
  and can NEVER bet in shadow. Promotion gate needs 15+ OOS settles, +CLV, ECE<=0.05,
  no drift, MANUAL operator review.
- Wiring is PURELY ADDITIVE SHADOW: import sportsedge; sportsedge_loop daemon runs a
  shadow pass every 20min; writes ONLY sportsedge_model.json; never touches account or
  trading path. CLI 'python3 bot.py sportsedge'; health.sportsedge field.
- VERIFIED live: one-shot pass against real ESPN+gamma — Elo learned 16 MLB teams;
  faced 26 sports-shaped markets (all Dota2/props/novelty) and priced ZERO (correct
  loud abstain — the anti-H#9). Restarted; loop ran 02:37, 0 errors. Suites: bot
  148/148, tests.py 80/80, sportsedge 10/10, ml + chartml PASS.
- DOES NOT touch is_in_game (live still banned for trading). The $5/$50 pre-game probe
  is unchanged. Live latency is a future MEASUREMENT step, not a trading lane.

## 2026-06-13 ~03:37 UTC — AUTOPILOT cycle

AUTOPILOT: shipped nothing — judge chose nothing: Ship nothing. I verified each proposal against the live code and data; none clears the bar of measured PnL/risk per unit of blast radius, and two of them rest on evidence that violates the hard rules.

1) Kelly fallback gate (conf 0.75) — FALSE PREMISE. The kelly stats table is band_win_stats() = backtest_results.json + research_results.json seed + live trades. I reconstructed it: every band 90-99 already has large n (band 92 ~152 obs, band 96 ~275, band 98 ~437). The n==0 pooling fallback the proposal targets is essentially never reached for the 92-96c bands it names. Its cited '92c 16.7% WR, n<20' numbers come from learning.json's per-strategy band tallies (tiny live n), NOT the kelly pooled table that actually sizes trades. The '98c n=440 with 431 wins yet still fails kelly_dollars gate' claim is internally incoherent — that n passes any gate. Simulating kelly_dollars on the real pooled stats shows it already self-defunds weak bands (90c, 91c -> $0 via Wilson-lower vs price). No defect; the fix would change nothing for the named bands.

2) Explorer pause rule (conf 0.92) — OVERRIDES DELIBERATE DESIGN. Lines 702-713 are an explicit, commented design: the explorer buys information, so win-streak rules are 'the wrong yardstick'; it pauses only when the $50 info budget is spent, and bad cells are pruned by band/category blocks (Crypto+Sports already blocked). It has used only -$5.39 of $50. Forcing multiplier 0.0 over a -$3.03 16-trade material streak (6% of sanctioned budget) defunds the information function and creates a recovery deadlock (paused -> no trades -> last16 can never return to >=0). This is a philosophy swap, not a bug fix; it fights the system's design rather than correcting an error.

3) MLP hidden sizing (conf 0.95) — ZERO LIVE IMPACT by the proposal's own words: 'MLP never selected as champion; XGB always chosen', 'Live P&L impact: $0'. Benefit is purely hypothetical (n>300 someday, MLP becoming champion someday). The -0.74 logloss is already harmless because selection correctly never picks it. Live settled money outranks model CV scores; improving a number on a dormant reserve model is activity, not evidenced value.

4) scores_loop reload (conf 0.92) — FALSE PREMISE, and the fix is a regression. scores_loop receives the live account dict by reference (bot.py:6025) and re-reads account['positions'] every iteration (bot.py:3249). settle_positions rebinds that key in the SAME dict (account['positions']=still_open, bot.py:4073) and appends new positions in place (4001); the main daemon loop never reassigns account. So new positions ARE visible to the probe. latencies=[] is explained by there being no open sports positions to arm (sports is banned in-game and blocked across strategies), not by staleness. The proposed load_account(load_config()) per iteration would give scores_loop a DIFFERENT dict from the live trading account, orphaning it — a real regression — for zero benefit.

5) Immediate learning save (conf 0.92) — DOUBLE-COUNTS DEAD-COHORT TRADES. Its evidence ('high_prob Sports: 12 settles, -$60.11, 11 material >= 0.15, should auto-block') is 11 of 12 dead_cohort (r90-lane sports, permanently excluded 06-12 by dead_cohort()). Correct material non-dead count = 0, which is exactly why no block fires and correctly so — the current code structurally cannot repeat those trades. The proposal counts excluded dead-cohort losses as live evidence to justify a block, directly violating era-hygiene ('Never count dead-cohort trades as evidence'). There is a real structural latency (monitor_pass settles don't refresh learning until the next run_pass), but the proposal's entire justification and claimed impact ('prevents follow-on Sports entries') rest on trades that can no longer occur, so the evidence is poisoned and the impact fictional.

Baseline confirmed healthy before deciding: bot 148/148, tests.py 80/80, daemon /api/health ok at 127.0.0.1:8765, working tree clean at HEAD a3f234f. Per the rules, when evidence is weak, false, or dead-cohort-poisoned, the honest action is to ship NOTHING.

Relevant files inspected: /Users/you/polymarket-bot/bot.py (dead_cohort 653, compute_learning 665, band_win_stats 1149, kelly_dollars 1185, scores_loop 3209, settle_positions 4024 / line 4073, run_pass 4641, monitor_pass 4630, scores_loop launch 6025), /Users/you/polymarket-bot/ml.py (fit_mlp 367), /Users/you/polymarket-bot/learning.json, /Users/you/polymarket-bot/paper_account.json, /Users/you/polymarket-bot/backtest_results.json, /Users/you/polymarket-bot/research_results.json, /Users/you/polymarket-bot/scores_stats.json, /Users/you/polymarket-bot/config.json.

2026-06-12 AUTOPILOT: shipped nothing — judge chose nothing: Ship NOTHING this cycle. Both proposals fail the bar.

PROPOSAL 1 (Kelly seed swap to research['by_bucket']['0-6h']) — REJECTED. It is directly contradicted by the codebase's own documented design history. The current family-union research['seed'] is a deliberate de-duplication fix: QUANT_LOG lines 1012-1013 ('Kelly seed now FAMILY-LEVEL (union table): raw seeding pseudo-replicated 299 crypto obs = 11 families. Funding bands the honest math defunds') and the code comments at bot.py:1093-1101 and 1168-1173 spell out that raw per-bucket counts inflate Wilson because repeated observations of the same market family are NOT independent. Proposal 1 wants to revert to exactly those raw, pseudo-replicated counts (by_bucket['0-6h'].n of 92-415 per band, vs the honest eff_n of 16-62 families). The scout's 'horizon-bleed' narrative inverts the real, already-diagnosed failure mode (sample non-independence). The cited +$1.33/cycle gain is a pure on-sample backtest number with zero live-settled support; the rules state live settled money outranks every backtest/sim, and this proposal has none. Medium blast radius (re-sizes all high_prob bands 90-99) on evidence that contradicts the repo's prior, hard-won conclusion. Net: would re-open a bug the desk already closed.

PROPOSAL 2 (wrap run_pass/monitor_pass in ACCOUNT_LOCK) — REJECTED. The TOCTOU race is real and honestly logged, but the SPECIFIC fix proposed is wrong and would cause a live regression. run_pass (bot.py:4641-4646) and monitor_pass (4630-4637) both call settle_positions (4024), which performs blocking Gamma API calls plus time.sleep(0.1) per leg inside its loop (4031-4039). The scout's own blast note concedes the main loop would hold the lock '~30-120s per run_pass.' But the fast lanes — arb_loop (every 2s, lock at 4228) and daytrade_loop (every 15s, lock at 4601) — hold ACCOUNT_LOCK only around the millisecond-scale mutation (open_position + save_account). Wrapping the full passes would make a slow lane hold the shared lock across blocking I/O for 30-120s every cycle, starving the fast lanes of the very opportunities they exist to catch. The desk already evaluated this exact change (QUANT_LOG 1122-1123: 'main-loop ACCOUNT_LOCK gap (conf 0.7, real but touching lock discipline at night is riskier than the torn-save it prevents — next deep review with full test plan)') and deliberately deferred it. A correct fix is finer-grained (lock only the mutation points inside settle_positions, not the I/O) — a different, larger change. Shipping the proposed version trades a rare accounting glitch for a continuous latency tax on live trading. The proposal's high confidence (0.92) is misplaced: it is confident about the bug, not about the safety of this fix, which inverts the lock's design intent.

One change per cycle, and when the strongest evidence either contradicts the codebase's own settled conclusion (P1) or describes a fix that regresses the live lanes (P2), the honest action is to ship nothing.

## 2026-06-13 ~06:02 UTC — CROSS-MARKET consensus shipped (Kalshi/PredictIt/Manifold + sportsbook, shadow + OOS-gated brain weave)

SHIPPED commit ea58b72 (FEATURE: cross-market consensus shadow + OOS-gated brain weave). Restarted the paper daemon onto it and verified live. Rollback point: HEAD~1 (95ed5df).

WHAT SHIPPED: a new crossmarket.py module (579 LOC) + bot.py wiring (263 LOC) that reads other prediction markets and sportsbook consensus at/before decision time and feeds it to the brain as a SIGNAL, never as blind divergence trading. Day-one neutral; it ramps up only where it measurably predicts.

SOURCES WIRED (reliability-weighted, all governed/timeout-bounded/fail-silent via the bot's _http_json + caching): Kalshi (weight 1.0, dollars+legacy-cents schemas, bid/ask mid fallback, drops zero-volume/unpriced), PredictIt (0.6), Manifold (0.2 — play-money, lowest reliability), and the Odds-API sportsbook source (de-vigged two-way) which is READ FROM os.environ and SILENTLY SKIPPED when ODDS_API_KEY is absent (.env never read/printed). A dead external API cannot stall or crash the daemon: gather_pool is fail-silent on total outage.

GATED WEAVE (not blind divergence): lookup(market) attaches ctx["xmkt_consensus"]/ctx["xmkt_divergence"] at entry-context build; None when there is no cross-market match (the common case). The signal earns sizing weight ONLY through the brain's existing OOS/credibility gate plus a by_crossmarket attribution bucket. NO FUTURE-DATA LEAKAGE: consensus prices are read at/before decision time; never post-entry/resolution. ERA HYGIENE: the crossmarket learner respects dead_cohort().

SHADOW-SCORED UNTIL PROVEN: crossmarket_loop runs a shadow pass and writes ONLY crossmarket_model.json; promotion gate needs 15+ OOS settles where consensus beats the market AND CLV>0 — it can NEVER bet in shadow. Surfaced on /api/health as the new "crossmarket" field (n / matched / verdict / updated) and CLI `python3 bot.py crossmarket`.

REGRESSION PROOF (the hard rule — identical brain behavior with no cross-market match): crossmarket self-test asserts "no match -> empty consensus (neutral common path)"; with xmkt features defaulting None the global model is unchanged on the common (no-match) path.

TEST TALLY: crossmarket.py self-test 22/22 PASS (incl. matcher accepts true Fed twin / rejects unrelated row / rejects futures / rejects $66k-vs-$68k number mismatch / accepts matching 68k / rejects wrong resolution day; reliability-weighted consensus; divergence vs PM price; de-vig normalization; grade promotes only on 15+ settles with consensus-beats-market + CLV>0, refuses below 15 / when consensus loses; odds_api skipped without key; gather_pool fail-silent on total outage; CLV sign-correct; and the neutral no-match regression). tests.py 6/6 PASS.

VERIFIED LIVE: restarted bot.py paper, slept 12s, /api/health at 127.0.0.1:8765 ok=true + audit=balanced + new crossmarket field present (n=0, matched=2, verdict="no data" — correct day-one shadow state). Exactly one bot (pgrep "bot.py paper" -> 2 = Python + caffeinate). Stable on recheck, no tracebacks in bot.log. Watchdog supervising; .autopilot_pause cleared after health confirmed.

ROLLBACK PLAN: if health had been bad -> git reset --hard HEAD~1 (95ed5df), restart same way. Not needed; ship healthy.

## 2026-06-13 ~06:43 UTC — feature: per-category brain specialists (OOS-gated partial pooling)

**Shipped** (commit 8c3c994). Extends the existing per-strategy specialist
machinery in `brain_train`/`brain_adjust` to a canonical bet-category key
(sports / crypto / weather / politics / macro / social, via `cat_key`). This
is a hierarchical / partial-pooling layer, NOT N independent models: the
global model still trains on every non-arbitrage, dead-cohort-filtered row and
remains the prior and fallback.

**Mechanism**
- For each category with >=20 dead-cohort-filtered rows, `brain_train` fits a
  specialist logistic AND scores its out-of-sample skill with the *same*
  expanding-window walk-forward CV the global model uses. Stored as
  `{w, oos_skill, n_eff, n}` in `BRAIN["cat_specialists"]`.
- `brain_adjust(strategy, price, ctx, side=None, category=None)` blends the
  global probability toward the category specialist ONLY when `oos_skill>0`
  AND `n_eff>0`, weighted by `cw = n_eff/(n_eff+60)`. A category shrinks fully
  to the global model until it earns divergence out-of-sample; otherwise the
  call is a byte-identical no-op.
- Category is threaded point-in-time from the position/opportunity at all three
  sizing sites (kelly/high_prob, news, daytrade) — never recomputed at decision
  time, no extra network call.
- Cache fix: a stale BRAIN lacking `cat_specialists` now forces a retrain.

**Gates (all passed)**
- bot.py test 164/164, tests.py 80/0, chartml.py ALL PASS, ml.py ALL PASS.
- Tightened no-regression proof on a FIXED MIXED-category dataset: global
  `cv_skill` (0.6729) and `brain_adjust(category=None)` (1.06931446...) are
  byte-identical to the pre-change behavior on the SAME mixed data.
- Era hygiene: dead-cohort sports rows never form a specialist; an
  OOS-negative category, a dead-cohort-only (n_eff=0) category, and an unknown
  category are all pure no-ops; a category with real OOS skill + credibility
  diverges.
- Global cv_skill on the live account: None before -> None after (only 4
  settled rows; below the 10-row OOS threshold either way — no regression). No
  category has 20+ live rows yet, so the layer is currently a no-op live and
  will engage automatically once a category accumulates enough labels.

**Dashboard**: per-category specialists (category, n, OOS skill, applied cw)
now surface on the Models tab under model 13.

Restarted pause-fenced; /api/health ok + balanced, exactly one bot.

## 2026-06-13 ~07:12 UTC — FEATURE: per-category SPORTS API features (commit c2d18db)

The "sports" category specialist now gets its OWN relevant API feature set,
fed point-in-time and learned only by the OOS-gated sports specialist — the
global model is provably untouched.

**Connector (fail-silent, governed, cached, key-gated)** — `sports_features()`:
- `game_state_risk`: ESPN scoreboard state (pre/post) via the existing
  `_espn_board()` (governed `get_json`, 20s timeout), cached 5 min. Live
  in-game is BANNED per rules; post-game sets an abstain flag (`gpost`). A
  raising/empty ESPN yields `[]` — never stalls or crashes the scan.
- `elo_fair_value`: Polymarket-trained Elo read point-in-time from
  `SPORTSEDGE["ratings"]` (trained ONLY on finals already seen — immutable per
  decision, never the current game's result). Neutral when a team is unrated.
- `sportsbook_consensus` + `spread_vs_consensus_div`: median de-vigged moneyline
  P(fav) from The Odds API, ridden off the existing xmkt snapshot and gated on
  the `oddsapi` source being present (key from `os.environ["ODDS_API_KEY"]`;
  absent -> source skipped silently -> feature neutral). fav-aligned.

**Brain wiring** — `_brain_x` gains `sb_cons, elo_fv, sb_div, gpost`, ALL
defaulting EXACTLY 0.0 on the common path. To keep the GLOBAL model byte-
identical (the MLP's weight init and the trees' splits depend on the exact
input-dimension set), these keys are STRIPPED via a new `_global_x()` from the
global logistic, the zoo/MLP, the per-strategy specialists, and online
learning. They survive ONLY in the per-CATEGORY sports specialist, the one
learner allowed to weight them. Attached to sports-market entry context only;
every non-sports market leaves them absent. `by_sports` attribution bucket added.

**Leakage / era hygiene**: every read point-in-time (game-state now, Elo from
prior finals, consensus from the concurrent snapshot — never a final score);
`dead_cohort()` already threaded through the per-category learner (training
rows AND credibility), so the dead 06-12 kelly-lane sports cohort cannot
pollute the new specialist.

**Gates (all passed)**
- bot.py test 176/176 (+12 new sports tests), tests.py 80/0, chartml.py ALL
  PASS, ml.py ALL PASS, crossmarket.py ALL PASS.
- No-regression proof on the FIXED MIXED-category dataset: global `cv_skill`
  (0.6729) and `brain_adjust(category=None)` (1.0693144597232576) are BYTE-
  IDENTICAL before/after. Feature-space isolation proven: the global model and
  per-strategy specialists carry NO sports keys; the sports specialist DOES
  learn `sb_cons` end-to-end on a consensus-driven fixture.
- Sports features default neutral with no ctx; explicit-None == absent;
  fail-silent neutral with no game-state/rating/key; consensus requires the
  Odds-API source (key-gated); edges signed and capped [-1,1].
- Global cv_skill on the live account: None before -> None after (7 settled
  rows, below the 10-row OOS threshold either way — no regression). The sports
  layer is a no-op live until the category accumulates 20+ labels and beats the
  base rate out-of-sample, at which point it engages automatically.

Restarted pause-fenced; /api/health ok + balanced, exactly one bot.

---

## 2026-06-13 — FEATURE: per-category CRYPTO API features (CoinGecko + Coinbase + Kraken)

Commit `6aadc8c`. Second per-category specialist feed (after sports), same
OOS-gated partial-pooling contract: the global model is always the prior and is
provably untouched on the common path.

**Connectors** (all free/keyless public endpoints, governed via `get_json`
[20s timeout, fail-silent -> None], wrapped in `_cached`):
- `_kraken_spread_bps(sym)`: live bid/ask from Kraken `/0/public/Ticker` (b, a
  top-of-book) -> `(ask-bid)/mid*1e4` bps, 60s cache. Crossed/empty book -> None.
- `_coingecko_spot(sym)` + `_crypto_spot(sym)`: Coinbase spot first (existing
  `_spot`, 60s), CoinGecko `/simple/price` as fail-silent fallback.
- `_crypto_vol(sym)` (existing, hardened): now DROPS the in-progress hourly
  candle (`rows[:-1]`) so realized vol is computed from CLOSED candles only —
  the live candle's close/high/low keep moving and would leak forward data.

**Features** (`crypto_features(m, price)`, crypto markets only; all default None):
- `spot_distance_pct`: signed (spot - strike)/strike from live spot, when the
  question carries a threshold (`parse_threshold`). Point-in-time.
- `realized_vol_hourly`: Kraken last 24 closed hourly candles, close-to-close.
- `bid_ask_spread_bps`: live Kraken ticker spread at request time.

**Brain wiring** — `_brain_x` gains `c_spotdist, c_rvol, c_spread`, ALL
defaulting EXACTLY 0.0 on the common path. They are STRIPPED via `_global_x()`
(now `_CAT_X_KEYS = SPORTS_X_KEYS + CRYPTO_X_KEYS`) from the global logistic,
the zoo/MLP, the per-strategy specialists and online learning, surviving ONLY
in the per-CATEGORY crypto specialist — the one learner allowed to weight them.
Attached to crypto-market entry context only (detected via cat_key/cluster/coin
keyword); every non-crypto market leaves them absent. `by_crypto` attribution
bucket added (spot>strike / spot<strike / at-strike).

**Leakage / era hygiene**: every read point-in-time (spot now, vol from CLOSED
candles only, spread top-of-book now — never a future settlement). Spot/vol/
spread all derive from market prices, no proprietary/latent signal, safe for
the MIXED-category regression test. `dead_cohort()` already threaded through the
per-category learner (training rows AND credibility).

**Gates (all passed)**
- bot.py test 187/187 (+11 new crypto tests), tests.py 80/0, chartml.py ALL
  PASS, ml.py ALL PASS. (crossmarket.py untouched — not run.)
- No-regression proof on the FIXED MIXED-category dataset: global `cv_skill`
  (0.6729), global weights (bias 1.5314, imb 1.5094), and
  `brain_adjust(category=None)` (1.0693144597232576) BYTE-IDENTICAL before/after.
  Feature-space isolation proven: the global model and per-strategy specialists
  carry NO crypto keys; the crypto specialist DOES learn `c_spotdist` end-to-end
  on a spot-distance-driven fixture.
- Crypto features default neutral with no ctx; explicit-None == absent;
  fail-silent neutral on a non-crypto market (no symbol, no network); spread
  math verified point-in-time; signed/scaled/capped to [-1,1] / [0,1].
- Global cv_skill on the live account: None before -> None after (9 settled
  rows, below the 10-row OOS threshold either way — no regression). The crypto
  layer is a no-op live until the category accumulates 20+ labels and beats the
  base rate out-of-sample, at which point it engages automatically.

Restarted pause-fenced; /api/health ok + balanced, exactly one bot.

## 2026-06-13 — FEATURE: per-category WEATHER API features (Open-Meteo ensemble + weather.gov/NWS)

Commit `bd74000`. Third per-category specialist feed (after sports, crypto), same
OOS-gated partial-pooling contract: the global model is always the prior and is
provably untouched on the common path.

**Connectors** (free/keyless public endpoints, governed via `get_json` [20s
timeout, fail-silent -> None], wrapped in `_cached`; the optional paid-tier
`OPENMETEO_API_KEY` is read from `os.environ` and forwarded when present, absent
-> keyless endpoint, source never gated off):
- `_openmeteo_ensemble(lat, lon, date_str, kind)`: ~30-member GFS ensemble daily
  2m-max/min (°C) per member from `ensemble-api.open-meteo.com/v1/ensemble`, 15min
  cache. Keyed by the TARGET date; a non-target date -> `[]` (no future leak).
- `_nws_point_forecast(lat, lon, date_str, kind)`: official weather.gov/NWS point
  forecast, fail-silent mean fallback when the ensemble blips, 1h cache. Keyless
  (NWS only needs the User-Agent `session` already sends).
- `_wx_geocode(city)`: Open-Meteo keyless geocoder (city -> lat/lon), 24h cache.
- `_wx_strike_c` / `_wx_strike_side`: parse the strike (-> °C, the forecast's
  native unit) and the market direction from the question TEXT only (point-in-time).

**Features** (`weather_features(m, price)`, weather markets only; all default None):
- `forecast_vs_strike` (`w_fcstrike`): (ensemble_mean - strike)/(hist_err+0.5) in
  °C, capped to spec range [-3, 3]. Mean from the ensemble, NWS fallback.
- `forecast_spread` (`w_spread`): member stdev / 2.5, [0, 1.5] — forecast
  uncertainty from ensemble disagreement (needs >=3 members).
- `model_agreement` (`w_agree`): fraction of members on the market's side of the
  strike, [0, 1]; None for range/exact markets (no single direction).

**Brain wiring** — `_brain_x` gains `w_fcstrike, w_spread, w_agree`, ALL defaulting
EXACTLY 0.0 on the common path. They are STRIPPED via `_global_x()` (now
`_CAT_X_KEYS = SPORTS_X_KEYS + CRYPTO_X_KEYS + WEATHER_X_KEYS`) from the global
logistic, the zoo/MLP, the per-strategy specialists and online learning, surviving
ONLY in the per-CATEGORY weather specialist — the one learner allowed to weight
them. Attached to weather-market entry context only (detected via cat_key/cluster/
`_wx_parse`); every non-weather market leaves them absent. `by_weather` attribution
bucket added (forecast>strike / forecast<strike / at-strike).

**Leakage / era hygiene**: every read point-in-time — the ensemble/NWS forecasts
are model runs issued AS OF NOW (not future observations), the strike is parsed
from question text, and the ensemble table is keyed by the target DAY's daily
field (a non-target date yields []). Ensemble disagreement is historical forecast
agreement, never outcome peeking. Temperature markets are independent of
`dead_cohort` (sports-only), which is already threaded through the per-category
learner (training rows AND credibility) unchanged.

**Gates (all passed)**
- bot.py test 200/200 (+13 new weather tests), tests.py 80/0, chartml.py ALL
  PASS, ml.py ALL PASS. (crossmarket.py untouched — not run.)
- No-regression proof on the FIXED MIXED-category dataset: global `cv_skill`
  (0.6729), global weights (bias 1.5314, imb 1.5094), and
  `brain_adjust(category=None)` (1.0693144597232576) BYTE-IDENTICAL before/after.
  Feature-space isolation proven: the global model and per-strategy specialists
  carry NO weather keys; the weather specialist DOES learn `w_fcstrike` end-to-end
  on a planted fixture. Live `brain_adjust` byte-identical with weather ctx absent
  vs explicit-None vs present-with-Weather-category (1.0039492824077103).
- Global cv_skill on the live account: None before -> None after (12 brain rows,
  below the OOS threshold either way — no regression). The weather layer is a
  no-op live until the category accumulates 20+ labels and beats the base rate
  out-of-sample, at which point it engages automatically.

Restarted pause-fenced; /api/health ok + balanced, exactly one bot.

## 2026-06-13 — Per-category MACRO API features (FRED) shipped

**What.** Gave the `macro` specialist its own point-in-time feature set sourced from
FRED (Federal Reserve Economic Data), added as a hierarchical/partial-pooling
layer that shrinks fully to the global model until the category earns divergence
out-of-sample. Three features, all defaulting EXACTLY neutral (0.0) when absent:
- `m_ratedev` (macro_rate_dev): market-implied Fed-rate target (parsed % from the
  question) minus the latest DFF observation, scaled to [-1.5, 1.5].
- `m_cpisurp` (macro_cpi_surprise): YoY CPI vs a contemporaneous consensus carried
  on the market, normalized by a 0.5% basis, clipped [-1.5, 1.5].
- `m_yieldsig` (macro_yield_signal): 10Y-2Y (DGS10-DGS2) spread regime —
  -1.0 inverted / -0.5 flat / 0.0 normal / 0.5 steep.

**Connector.** `_fred_latest`/`_fred_value`/`macro_features` — KEY-GATED (reads
`FRED_API_KEY` from os.environ; absent -> every read returns [] / None silently,
so all three features stay neutral and the global path is unchanged). Governed via
`get_json`/`_governor`, timeout-bounded (20s), cached 1h, fail-silent (a dead or
key-less source yields None, never an exception or a stall).

**Wiring.** `is_macro` detection (cat_key=="macro" or rate/CPI/yield subject
regexes) gates a macro-only entry-context read; the three `macro_*` ctx fields are
attached to opportunities. `_brain_x` maps them to `m_ratedev/m_cpisurp/m_yieldsig`,
defaulting 0.0 when absent. `MACRO_X_KEYS` added to `_CAT_X_KEYS` so `_global_x`
STRIPS them from the global logistic/zoo/MLP view — they survive ONLY in the
OOS-gated macro specialist. brain_adjust's per-category partial pooling already
engages the macro specialist when (and only when) it earns oos_skill>0 with n_eff>0.

**Leakage / era hygiene.** Point-in-time verified: FRED observations publish with a
real lag (CPI ~12d, DFF ~1d) so the latest available value carries no forward
knowledge; rate/CPI expectation is today's market consensus, not a forecast; the
yield curve is the latest published Treasury yields. dead_cohort() remains threaded
through every per-category learner (training rows AND credibility). The specialist
is a pure no-op until 20+ living-cohort labels beat the base rate OOS.

**Global no-regression (proven).** Golden global cv_skill on the FIXED MIXED-category
fixture: 0.6729 BEFORE == 0.6729 AFTER; golden global weights unchanged
(bias 1.5314, imb 1.5094); zero macro keys in the global w or any per-strategy
specialist. 14 new macro tests (neutrality, explicit-None==absent sentinel,
signed/scaled/capped values, key-gating silent-skip, fail-silent no-network leakage,
point-in-time FRED read skipping missing "." values, end-to-end specialist learning
+ feature-space isolation).

**Gates.** bot.py 214/214, tests.py 80/80, chartml.py ALL PASS, ml.py ALL PASS.
crossmarket.py untouched. Committed (FEATURE: macro API features). Restarted
pause-fenced; /api/health ok + balanced, exactly one bot.

## 2026-06-13 — FEATURE: per-category SOCIAL API features (news_rss + HackerNews) shipped (commit 6038018)

**What.** Gave the `social` specialist its own point-in-time feature set sourced from
the keyless news_rss (Google News + BBC) and HackerNews connectors, added as a
hierarchical/partial-pooling layer that shrinks fully to the global model until the
category earns divergence out-of-sample. Three features, all defaulting EXACTLY
neutral (0.0) when absent:
- `s_newsstrong` (social_news_strong): corroborated fresh-coverage flag — 1.0 when
  >=2 distinct fresh headlines match the subject or one matches strongly (>=3
  overlapping tokens). Stronger than the GLOBAL single-hit `newsbk`.
- `s_sentmag` (social_sent_mag): magnitude of fresh-headline lexicon sentiment, [0,1]
  — how loud the news is, direction-agnostic.
- `s_sentalign` (social_sent_align): sentiment aligned to the side being bet, [-1,1]
  — positive mood on a Yes (or negative mood on a No) reads positive; the No side
  flips the sign.

**Connector.** Both APIs ALREADY exist in `news_loop()` and fill the shared
`HEADLINES` buffer: news_rss (Google News + BBC RSS, real pubDate via `_rss_items`)
and HackerNews (hn.algolia.com search_by_date, `created_at_i`). Keyless, governed
(`_governor`), timeout-bounded (10s), cached, fail-silent — a dead feed yields fewer
fresh hits, never an exception or a stall. The new `social_features(question, side)`
does ZERO network I/O itself (it only reads the buffer the loop owns), so it can
neither stall nor crash the daemon. `_fresh_headline_hits` helper added.

**Wiring.** `is_social` detection (cat_key=="social" or cluster_of=="social-posts")
gates a social-only entry-context read; the three `social_*` ctx fields are attached
to opportunities, side-aligned to the outcome we'd back. `_brain_x` maps them to
`s_newsstrong/s_sentmag/s_sentalign`, defaulting 0.0 when absent. `SOCIAL_X_KEYS`
added to `_CAT_X_KEYS` so `_global_x` STRIPS them from the global logistic/zoo/MLP
view — they survive ONLY in the OOS-gated social specialist. The GLOBAL `newsbk`/
`nsent` keys are DISTINCT and unchanged. brain_adjust's per-category partial pooling
already engages the social specialist when (and only when) it earns oos_skill>0 with
n_eff>0. Added a `by_social` attribution bucket (scoped to social_sent_align).

**Leakage / era hygiene.** Point-in-time verified: every headline carries its REAL
publication timestamp (RSS pubDate / HN created_at_i, never fetch time) and is
filtered to a strict 5400s freshness window BEFORE the decision moment — a regression
test plants the same two headlines OUTSIDE the window and proves they are invisible
(the exact future-leakage guard). No future market data is consulted. dead_cohort()
remains threaded through every per-category learner (training rows AND credibility).
The specialist is a pure no-op until 20+ living-cohort labels beat the base rate OOS.

**Global no-regression (proven).** Golden global cv_skill on the FIXED MIXED-category
fixture: 0.6729 BEFORE == 0.6729 AFTER; golden global weights unchanged
(bias 1.5314, imb 1.5094); golden brain_adjust(category=None) on MIXED data
1.0693144597232576 unchanged; zero social keys in the global w or any per-strategy
specialist. Live global cv_skill None BEFORE == None AFTER (only 20 living non-arb
rows, n_eff=5 — below the CV threshold, unchanged). 12 new social tests (neutrality,
explicit-None==absent sentinel, binary/scaled/capped values, side-alignment flip,
stale-headline no-future-leak, fail-silent off-subject neutral, end-to-end specialist
learning + global feature-space isolation).

**Gates.** bot.py 226/226, tests.py 80/80, chartml.py ALL PASS, ml.py ALL PASS.
crossmarket.py untouched (ran for sanity, ALL PASS). Committed (FEATURE: social API
features (news_rss+hackernews)). Restarted pause-fenced; /api/health ok + balanced,
exactly one bot.

2026-06-13 AUTOPILOT: shipped nothing — judge chose nothing: Ship NOTHING this cycle — no proposal clears the bar of measured live evidence with blast radius proportional to it. I verified every cited line in /Users/you/polymarket-bot/bot.py against the live tree and inspected paper_account.json (33 open, 23 settled, 0 dead-cohort trades). P4 is the best-behaved proposal: the code asymmetry it describes is real (brain_train at line 2181 filters dead_cohort(t); brain_online_learn at line 2476/5279 does not), the fix is safe, additive, and tiny. But it is an inert no-op: there are zero dead-cohort trades, and the current code STRUCTURALLY cannot create new lane=r90 sports trades — the lane90 sports exclusion at line 4808 and the dead_cohort definition (line 690, 'excluded permanently on 06-12') guarantee it. For the bug to manifest, current code would have to do the exact thing it is built to refuse. The proposal itself admits 'Measured improvement: none (latent bug, not yet manifested).' Per the rules, shipping a zero-impact change protecting against a condition current code cannot produce is activity for its own sake. The other three are weaker: each rests on absent or anti-evidence, and P2 brushes a HARD safety rail.

## 2026-06-13 — Cycle 1: SHIPPED — Wire daytrade_loop to respect blocked_categories gate

**Commit:** 98f0ceb4ad108375d9402fffb758a85c3b13b2db (bot.py, +2 lines)
**Rollback target:** HEAD~1 = 6880f53d40d2caa557a10fa1273f55145d012c42

**Change:** In `daytrade_loop`, after `dt_category = market_category(m)` (~line 5798), added:
`if dt_category in (dt.get("blocked_categories") or []): continue`
This mirrors the existing gate in `scan_high_prob` (line 4847). Previously daytrade_loop
computed the learning state and read dt_category but never consulted blocked_categories,
so the learning system's category bans were silently ignored on the daytrade path.

**Evidence:** Backup state (Jun 12 20:54 UTC) showed daytrade blocked_categories=['Sports']
after 7 settles, -$13.33 P&L (1W/6L), multiplier 0.5. Current state (Jun 13 08:40 UTC) had
1 settle (Colorado Rockies sports trade, -$4.50), no blocks, multiplier 1.0. The Rockies
trade at 03:58 UTC was a LIVE game (ESPN score event +3.3s later), violating
min_hours_to_resolution. Root cause: the daytrade path lacked the blocked_categories check
that scan_high_prob already has. This is a wiring fix, not a new strategy or a relaxed gate.

**Test tally:** Adversarial review passed pre-ship (change committed at HEAD). Diff verified
additive (1 file, +2 lines) and identical in pattern to the proven scan_high_prob gate.
Degrades to always-pass when no categories blocked (safe default). No safety gate, sports
in-game ban, probe rail, or loss breaker touched.

**Ship verification:** Restarted onto 98f0ceb under .autopilot_pause fence. Health post-restart:
ok=true, audit=balanced, age_seconds≈17. Exactly 1 python + 1 caffeinate confirmed. Watchdog
re-armed (pause flag removed). Equity before=$9999.63, after=$9999.59 (mark drift only).
Settles at ship: 34. Marked pending_unproven=true — promotion to be judged on future live settles.

2026-06-13 AUTOPILOT: shipped nothing — judge chose nothing: Ship NOTHING. No proposal clears the bar; two violate hard rules, one guards an untriggerable path, and the cleanest delivers no measurable PnL.

Proposal 1 (uncap Kelly) is forbidden: line 4837 `dollars = min(dollars, max_dollars)` is an explicit per-trade risk rail (code comment lines 4698-4699: a hold-to-resolution binary's full stake is capped at ~1% of bankroll). Removing it lets single positions reach $335-516 (3.35-5.16% of bankroll, 1.7-2.6x larger) — weakening a risk rail. The evidence is pure backtest seed data (research_results.json, observational, no live settles), and the live paper_account has ZERO high_prob settles in any band: the bands 91-94 it wants to upsize have never produced a single live settle. Sizing-up on a backtest directly contradicts 'Live settled money outranks every backtest' and 'Promotion decides sizing, not enthusiasm.'

Proposal 2 (cap Platt a) rests on mistaken evidence. Its edit target, line 2329, is `out['cal'] = ml.fit_isotonic(preds)` — the isotonic branch, which has no `a` parameter, so the guard would not reliably touch Platt. The brain's cal only feeds p_model on the stack branch, and the resulting tilt is credibility-shrunk (n_eff=9 -> cred ~= 9/69*0.5 ~= 0.065) and hard-bounded to [0.4,1.6] in brain_adjust (line 2473). The claimed 'inflates Kelly sizing by ~40%' misrepresents the mechanism; there is no such direct sizing channel. Weak, mislocated.

Proposal 4 (dead_cohort guard in brain_online_learn) targets a real but untriggerable gap. Verified: lane r90 settles = 0; the only 2 open Sports positions have lane=None (not dead-cohort) and are explore-strategy. No dead-cohort position can settle to trigger this path, and brain_train's walk-forward CV (which has the filter, commit 8c3c994) would cleanse any contamination at next retrain. The proposal itself admits 'Current live risk low.' Guarding an untriggerable path is activity for its own sake.

Proposal 3 (sportsedge esports filter) is the cleanest — genuinely shadow-only ('Trades NOTHING', line 3906), touches no gate or rail — but its own evidence shows near-zero value: 'No direct P&L impact in shadow mode,' ~$0.10-0.50/week eventual indirect impact, from a single thin 2026-06-13 measurement. Low blast radius but no measured PnL. Plumbing, not a win.

When evidence is backtest-only, misdiagnosed, untriggerable, or PnL-negligible, the honest default is to ship nothing.

---

## 2026-06-13 — Cycle 2 — SHIPPED: in-game Sports gate in daytrade fast loop

**Commit:** c9e5d59 "AUTOPILOT: gate in-game Sports out of daytrade fast loop"
**Rollback target:** HEAD~1 (ef6632b)

**Change:** Inserted `if is_in_game(m): continue` in `daytrade_loop()` (bot.py ~line 5776), before the `chartml.move_predict` call. Reuses the existing, well-tested `is_in_game()` (defined line 4602). This brings the fast daytrade loop to parity with `scan_news()` which already gates in-game markets (line 5611). +4 lines (1 logical gate + comment), no other code touched.

**Evidence (live settled money — strongest class):** paper_account.json shows exactly 2 daytrade settles, BOTH Sports, BOTH losses: -$4.50 and -$3.35 = -$7.85 total, 0% win rate (2/2). Both had strong chartml revert signals (0.76, 0.607) yet lost — in-game Sports are jump-process prices where the move model's mean-reversion assumption (trained on 6,955 labeled events, 63.4% base revert, +0.0994 OOS skill on 1,703-event chronological holdout) breaks down. The slow scan_news path already gates this; the fast daytrade loop did not. dead_cohort() present (line 688) for era hygiene; the 2 daytrade settles are current-code-producible.

**Why safe / blast radius:** Low. This strictly TIGHTENS daytrade entry — it removes/weakens no safety gate, rail, or breaker. Pure gate ADDITION. No impact on high_prob, news, explore, arbitrage. Pre-game and non-Sports daytrade opportunities unaffected. Model unchanged; only its deployment gate improved.

**Verification tally:** ast.parse SYNTAX OK; is_in_game def confirmed (line 4602); scan_news parity gate confirmed (line 5611); gate placement before move_predict confirmed (live line 5779). Post-restart health: ok=true, audit=balanced, age_seconds=16.7. Bot proc count (wc -l)=2 (python+caffeinate), python-only=1. HEAD=c9e5d59, working tree clean. Pre-ship cash $9399.88; post-ship cash $9399.88 (no settles during restart). pending_unproven=true — promotion to higher confidence only after live settled confirmation.

## 2026-06-13 — AUTOPILOT: shipped nothing

AUTOPILOT: shipped nothing — judge chose nothing: Ship NOTHING. I verified every cited line against the live tree (HEAD 10c697c) and inspected paper_account.json: 64 settles = 61 explore + 3 daytrade (all losses, -$8.40 total), ZERO dead-cohort settles, ZERO high_prob settles in any band, daemon healthy (ok=true, balanced, 2 procs). No proposal clears the bar of current-code-producible live settled evidence with blast radius proportional to it.

P4 (is_in_game gameStartTime bypass) is the headline rejection: its core gate — `if is_in_game(m): continue` in daytrade_loop (line 5777) — ALREADY SHIPPED last cycle (commit c9e5d59, 2026-06-13 10:46 UTC) and is live. Its novel claim that the live gate silently leaks because the bulk API omits gameStartTime is unproven and contradicted by the data: the two fast-loop sports losers (Rockies BUY 03:58 UTC, Stuttgart-Sho BUY 10:11 UTC, both 06-13) were entered BEFORE the gate existed (10:46 UTC) — they are pre-gate losses, not proof the live gate fails. Moreover `is_in_game({}) is False` is an intentional, tested contract (line 7553, mirrored in sports_probe_ok line 7730 and lane90 line 4808). The fix would add per-cycle market-detail fetches and a new path that rejects sports-shaped markets lacking gameStartTime as 'unreliable' — new network + new sports-surface rejection behavior with zero measured evidence it's needed. Its own counts are wrong (claims 5 entries / -$10.40; actual 3 / -$8.40). Blast radius out of proportion to absent evidence.

P1 (risk_per_trade_pct 2%->3%) is forbidden in spirit: pure backtest/seed data, and the 93-99c bands it wants to upsize have produced ZERO live high_prob settles. Sizing up on a backtest violates 'Live settled money outranks every backtest' and 'Promotion decides sizing, not enthusiasm'; it also widens the per-trade risk rail (line 4837 cap) by 50%. The structurally identical Kelly-uncap proposal was already rejected for these reasons. The '+$5120/day' figure is fabricated extrapolation.

P2 (ml_min_revert 0.5->0.77) is overfitting to n=3: the threshold is reverse-engineered to reject exactly the 3 historical losers (chart_ml 0.76/0.607/0.55). Two of those losses are in-game sports jump risk already handled by the shipped is_in_game gate; cherry-picking a cutoff over a 3-sample tail is not honest evidence.

P3 (HN created_at_i truthiness) is a real but speculative latent bug: line 3761 `ts = hit.get('created_at_i') or now` could map created_at_i==0 to 'now', but the proposal admits 'Measured impact: none yet,' news is throttled x0.5 / Sports-blocked / 6h-gated, and there is zero evidence any epoch-0 HN article has ever leaked. That is deferred plumbing for a future un-throttle, not a measured win.

When evidence is backtest-only, already-shipped, overfit to a 3-sample tail, or an unmanifested latent bug, the honest default is to ship nothing.

2026-06-13 AUTOPILOT: shipped nothing — adversarial review killed it: failed review (0/3 cleared). commit cd6aaa0

## 2026-06-13 — manual fix: daytrade stop bleeding (user "it's losing")
- Diagnosis: account ~flat (-0.07%, $9,992.63). Sole bleed = daytrade 0/4, -$11.40. explore +$4.05 (77), arb/high_prob flat.
- The 4 daytrade losers: 2 Sports (Rockies, Stuttgart — in-game stop-outs, ALREADY gated by is_in_game c9e5d59) + 2 Politics (Trump, Iran — faded news-driven event moves that drifted).
- Root cause of the un-gated 2: both daytrade paths faded moves WITHOUT checking news_backed. news-backed moves drift on the information; fading them is "how fades die" (the flag's own docstring). Daytrade ran at full size (mult 1.0) because the learning throttle only kicks in at 8 settles (had 4).
- Fix (978c6b3): added `news_backed()` skip to BOTH the fast daytrade_loop and the slow scan path, parity with is_in_game. Additive gate, no safety rail touched. All 4 suites green (226/226 + 80 + chartml + ml). With this + the in-game gate, all 4 historical daytrade loss modes are now structurally gated.
2026-06-13 AUTOPILOT: shipped nothing — adversarial review killed it: failed review (1/3 cleared). commit 6e98b1a

## 2026-06-13 — manual: bigger heavy-favorite (high_prob) sizing (user request)
- quant.kelly_fraction 0.25 -> 0.40; high_probability.risk_per_trade_pct 4.0 (NEW hp-specific) + max_dollars_per_trade 300 -> 450; 1-line code change so hp cap uses its own risk %, not global.
- Effect: hp per-trade cap $200 -> $400 (2x), more aggressive Kelly. daytrade/news/explore UNCHANGED (global 2% kept). 20% portfolio heat cap + loss breaker intact -> total risk still bounded.
- Note: high_prob has 0 settled trades yet, so this is a conviction bet on the backtest-validated 96-98.9c favorite edge, not live-proven. Reversible. Suites 226/226+80+chartml+ml green; restarted healthy. Commit 463f8fe.

## 2026-06-13 — manual: kill the sports bleed ("not looking good")
- Diagnosis: equity flat (-0.10%). ALL loss is sports: -$12.92 sports vs +$2.97 non-sports; explore non-sports +$6.69 @93% vs explore sports -$5.07 @25%. Reset wiped learning.json; explore re-learned blocked_categories=[Sports] but the block only checked the gamma TAG, so untagged foreign football slipped through.
- Fix: explore/high_prob block now ALSO uses is_sport (_SPORTSY name detector) when Sports is blocked. Removes the entire bleed; bot is net-positive on its real (non-sports) edge. 226/226+80+chartml+ml green. Restarted healthy.

## 2026-06-13 — AUTOPILOT: shipped nothing
AUTOPILOT: shipped nothing — judge chose nothing: Verified every proposal against the live code and paper_account.json; all six fail. Shipping nothing is the honest action this cycle.

P1 (Kelly fix) is mathematically WRONG and would be actively harmful. I checked bot.py:1231 with exact-fraction algebra over thousands of points: the current formula `f = p - (1-p)*price/(1-price)` is EXACTLY the standard Kelly risk-fraction for a Polymarket buy-a-share-at-`price` binary bet (identical to `(b*p-q)/b` with `b=(1-price)/price`). The proposed replacement `p*(1-price)-(1-p)*price` algebraically simplifies to just `p - price` (expected profit per share, not a bankroll fraction), which would undersize ~5-12x. The scout's 'verification' used the wrong payoff structure (symmetric two-sided stake, not Polymarket's). The code is already correct. Also moot: high_prob has only n=1 clean settle.

P2 (brain_adjust) misrepresents the formula. bot.py:2473 uses `1.0 + 2.0*cred*(p_model - price)` where `p_model` (lines 2450-2469) is the brain's LEARNED logistic/specialist probability, not the raw quote. It sizes up on positive model edge — correct direction. The scout's worked example substitutes a constant 0.505 for p_model, dropping the model term entirely; that is a fabrication. Medium blast radius (all strategies) on n=1 high_prob + dead-cohort daytrade is unjustified.

P3 (defund daytrade -> explore) violates era hygiene. All 5 daytrade losses (-$12.50) are dead cohort under current gates: 2 are in-game Sports `vs.` fades blocked by is_in_game (bot.py:5789) + the tightened Sports block, and 3 are news-backed Politics fades blocked by news_backed (bot.py:5793, shipped today at 05:58 whose commit cites 'exactly the Politics daytrade stop-outs we just took'). Daytrade has ZERO clean post-gate settles, so judging it negative counts trades the current code cannot reproduce. The explore side is real (non-sports n=97 +$7.83) but explore uses fixed $1 stakes and is nowhere near its $1000 sub-account cap, so the $1000->$1500 bump is cosmetic. Can't ship the sound half without the unsound half.

P4 (sports_probe on weather) has a false premise. sports_probe=1 is set only inside `if is_sport and use_kelly` (bot.py:4896/4907). For the flagged weather markets, current is_sport is False: category is Weather (not Sports), `_SPORTSY` (bot.py:4623) doesn't match 'highest temperature' names, cluster_of returns 'weather' not 'sports-game' (CLUSTERS bot.py:1760, weather ordered first), and weather markets have no gameStartTime. I verified the regex returns False on all 8 names. So HEAD cannot tag weather as sports_probe; the 8 tags are a legacy cohort from older broader detection. The proposed `and category=='Sports'` gate would also narrow the probe to tag-only Sports, reintroducing the missing-tag blind spot the codebase just closed for obscure foreign football.

P5 (chartml lookback) is refuted by data: it claims all 5 daytrade settles have chart_ml=None, but all 5 have chart_ml populated (0.76, 0.607, 0.55, 0.541, 0.555). chartml already executes on daytrade entries (bot.py:5800). Premise false.

P6 (wallet TTL 6h->1h) is the weakest (conf 0.72): speculative revival of an unmeasured 'smart_agree' signal (0 live settles) on a 'documented insider pattern' assertion — activity for its own sake, no measured edge.

Working tree clean, HEAD known-good. No proposal clears the bar; ship NOTHING.

2026-06-13 AUTOPILOT: shipped nothing — judge chose nothing: Ship NOTHING. I verified every proposal against on-disk data (paper_account.json 129 settles, learning.json, config.json, brain.json), the live daemon (RSS 1916 MB), the actual code paths, and prior QUANT_LOG verdicts. Zero of the 129 settles are dead-cohort, so the aggregate numbers are admissible, but each proposal fails on grounds beyond era hygiene. None clears the PnL/risk-per-blast-radius bar, and several rest on fabricated or backwards evidence. The honest default applies.

2026-06-13 AUTOPILOT: shipped nothing — judge chose nothing: Ship nothing. I verified each proposal against the live code (bot.py) and paper_account.json (149 settles), and the three load-bearing proposals rest on misreadings of the codebase, while the fourth has zero measured impact.

Proposal 1 (daytrade confidence gate): Its evidence describes a calibrated Yes-probability p_model (Platt/isotonic) being compared to price, but daytrade_loop() (bot.py ~5710-5870) has no such variable. The only model probability there is chartml's p_rev (P(move reverts)), which is not comparable to price. The proposed |calibrated_p - price| < 5% gate cannot be implemented as described. The +$12.50 'recovery' is retro curve-fitting to 5 losses.

Proposal 2 (reallocate daytrade->explore): Its central claim that all 5 daytrade settles are dead cohort is FALSE under the project's own dead_cohort() (bot.py:688). I ran the actual function: all 5 daytrade trades have lane=None (not r90), so dead_cohort()=0/5. The proposal substitutes a self-invented is_in_game/news_backed definition to dismiss the -$12.50 — exactly the dead-cohort manipulation the rules forbid. The explore upside is also illusory: explore is capped at max_dollars_per_trade=1.0 with only ~$15 of $987 deployed, so adding $250 to a 98.5%-idle budget yields nothing.

Proposal 3 (calibration_table condition): The stated mechanism is wrong. The loop at bot.py:2303 adds every stack member to tr_models by name, including logistic (top stack weight 0.262), so logistic IS in tr_models. brain.json's null calibration_table is caused by champ=None (a stack with no single named champion), upstream of the edited condition. The change wouldn't fix it, and the proposal admits 'No direct PnL' — pure scaffolding on a misdiagnosis.

Proposal 4 (dead_cohort filter in explorer_proven_bands): Accurate and harmless — the function (bot.py:4515-4527) genuinely lacks the dead_cohort filter its 5 sibling learners have. But it correctly notes 0 dead-cohort explores exist today (verified), so live impact is exactly zero. It guards a future r90-explore path that doesn't currently exist. A correct-but-zero-impact prophylactic isn't worth the cycle's single shipped change; the honest default is to ship nothing rather than ship for activity's sake.

Live settled money outranks every model story here, and the strongest 'evidence' (Proposals 1-3) is built on code paths that don't behave as claimed. The honest action is to ship nothing this cycle.

2026-06-13 AUTOPILOT: shipped nothing — judge chose nothing: Verified all four proposals against bot.py, config.json, and paper_account.json on the live paper account. None clears the bar; the honest action is to ship nothing.

P1 (compute_learning threshold 8->4, bot.py:723): The mechanism is real, but this weakens a deliberately-calibrated statistical guardrail SYSTEM-WIDE (all strategies) to react to one strategy's tiny streak. Its evidence is 5 daytrade losses, but 2 are in-game Sports fades (Rockies, Shelton, chart_ml 0.76/0.607) that the is_in_game gate (commit c9e5d59) now structurally blocks — era-impure, cannot testify about the living code. Honest repeatable n=3. Halving the sample-size protection on n=3 is wrong-altitude noise-chasing. Reject.

P2 (sports_probe weather guard, bot.py:5093): Highest claimed confidence (0.95) but the diagnosis and fix are both wrong. Empirical claim is true — 8 weather/temperature markets are tagged sports_probe=1 (1 settled +$0.03, 7 open). But sports_probe is set at line 4907 under `if is_sport and use_kelly:` (line 4896), already gated on is_sport. The contaminated trades came through the KELLY lane (context carries lane/kelly_dollars/oracle_*/probation), not the line-5093 block the proposal edits (that block carries macro_*/social_* keys). The proposed `and is_sport` at 5093 is both misplaced and redundant — it would not stop the contamination. Real root cause is is_sport itself returning True for weather (cluster_of/gameStartTime at scan time), unaddressed. Shipping it is activity that fixes nothing. Reject.

P3 (daytrade ml_min_revert 0.5->0.60, config.json): Best candidate — smallest blast radius (one config line, daytrade only), touches no safety rail, reversible. Gate is real (bot.py:5612-5613, 5803; default 0.5). The 3 repeatable Politics fade losses have chart_ml 0.55/0.541/0.555, all in [0.5,0.6), so 0.60 would block them. BUT the field is mislabeled (it is chart_ml, not p_rev), and the honest repeatable sample is only n=3 — all in one narrow band — with no holdout showing 0.60 is the right cut rather than 0.56/0.58. This is curve-fitting a model threshold to a handful of adjacent live points. n=3 is below the bar this codebase sets for itself (its own band/category blocks need n>=6, learning needs n_mat>=8, sports promotion needs 15+). The existing last8 throttle will halve daytrade size honestly once it reaches n_mat=8. Not yet.

P4 (crossmarket force=True, bot.py:4215): Code claims check out (TTL=600, force=True in shadow at 4230), but the feature is shadow-only with ZERO live trades (confirmed) and the proposal itself concedes impact is speculative until promotion. No measured PnL; adds entry-path fetch overhead. Live settled money outranks model theory. Reject.

Verdict: P1/P2/P4 fail outright. P3 is the only thing with measured live losses and a rail-safe, correctly-located change, but it rests on n=3 and a hand-picked threshold with no holdout — parameter-fitting dressed as prudence. Per the one-change-per-cycle and statistical-honesty rules, ship NOTHING.

## 2026-06-13

AUTOPILOT: shipped nothing — judge chose nothing: Ship nothing. None of the three proposals clears the bar of measured edge per unit of blast radius, and a prior change (c9e5d59) is still pending_unproven at only 52 settles.

Proposal 1 (kelly_fraction 0.40->0.65): REJECT. It scales the single global full-Kelly multiplier up 63%, raising position risk across all high_prob trades, on the strength of a strategy whose era-clean evidence is exactly ONE material non-dead-cohort settle (+$0.27 — the r90/Crypto Bitcoin Up-or-Down). Live high_prob is 2/2 / +$0.30 total. That is promotion by enthusiasm, not by measured edge, which the rules forbid. The proposal also misdescribes the live config (claims a 90-96c band and bankroll/lane numbers that do not match config.json, where high_prob is 0.96-0.989). Its own confidence is 0.45. Verified the Kelly multiplier line is config.json quant.kelly_fraction=0.4.

Proposal 2 (invert brain_adjust gate to block adj>0.95): REJECT — most dangerous. Its causal model is mathematically backwards. The formula at bot.py:2473 is max(0.4,min(1.6, 1.0 + 2.0*cred*(p_model - price))), so adj>1.0 means the brain thinks the true probability EXCEEDS the market price (better edge -> upsize), NOT 'worse edge' as the proposal asserts. Live settles confirm the entire profitable explore book (153 trades, +$3.87, 88.9% wr) sits at adj~0.98-1.0; a gate blocking adj>0.95 would block essentially all of explore — the only winning strategy. The proposal is internally contradictory (says keep the adj~1.0 winners while proposing a threshold that blocks them). The news/daytrade losses it targets are not cleanly above 1.0 either — the two largest news losses (-$4.80) are at adj<0.95, so the gate would not even prevent them.

Proposal 3 (24h TTL on SCORE_STATE): Real but not worth this cycle. The bug is genuine — SCORE_STATE (bot.py:3781, written at 4349/4364) has no expiry, and I confirmed one stale artifact: scores_stats.json holds a 2008.5s lag sample (vs median 12.6s) that is almost certainly a stale-key match. The change is small, local, mirrors the existing ARMED_SCORES 300s cleanup (bot.py:4378-4380), and is reversible. BUT it has zero PnL and zero risk impact by the proposal's own admission, because sports is gated from trading. It would clean telemetry on a lane producing ~5 events / 4 lag samples total — activity for its own sake. With one-change-per-cycle and a prior change still unproven, the honest default is to ship nothing rather than spend the cycle on cosmetic infrastructure hygiene with no live-money consequence.
