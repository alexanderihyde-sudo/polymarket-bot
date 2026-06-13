# Architecture — Polymarket Paper-Trading Bot

A complete autonomous trading system: five strategies, fifteen risk/learning
models, a three-member ML committee, eight external data feeds, professional
bankroll management, and a self-documenting dashboard — paper money only,
real market data, every component judged by settled dollars.

## The one-page mental model

```
                       ┌─────────────  DATA FEEDS  ─────────────┐
  Polymarket books (1s bulk) · trade tape (whales) · 3 score feeds
  3 news feeds · weather forecasts · crypto spot+vol (2 exchanges)
                       └───────────────┬─────────────────────────┘
                                       ▼
   SCANNERS (per strategy)  ──gates──►  ENTRY  ──►  POSITIONS
   arbitrage 1s · pairs · favorites · explorer · fast daytrade 15s
                                       │
        models 1–15 vote ──────────────┤    1-second price monitor:
        brain committee tilts sizing ──┤    stops · targets · models
        bankroll mgr caps risk ────────┘    9/10/12 · latency probes
                                       ▼
                                   SETTLES
                                       │
        ┌──────────────────────────────┼─────────────────────────┐
        ▼                              ▼                         ▼
   LEARNING TABLES               THE JOURNAL                ATTRIBUTION
   bands · categories       every move, full context      P&L by every
   streaks (material        (decisions.jsonl)             decision signal
   evidence only)                                         judged 2-hourly
        │                              │                         │
        └────────────► EVIDENCE-GATED CHANGES ◄──────────────────┘
              bot self-adapts each settle · Claude ships one
              improvement per review · evolver re-derives 6-hourly
```

## Files

| File | Role |
|---|---|
| bot.py | the engine: strategies, models, scanners, risk, server |
| ml.py | ML library: GBM, XGB, forest, MLP, calibration, drift |
| chartml.py | chart & pattern ML: move model, miner significance |
| tests.py | deep regression suite (every shipped bug becomes a test) |
| config.json | live-tunable settings (hot-reloaded every pass) |
| dashboard.html | the terminal UI (3 synced charts, tabs, legends) |
| QUANT_LOG.md | dated verdicts from every review — the changelog of why |
| HYPOTHESES.md / TRICKS.md | falsifiable backlog; tested items get verdicts |
| decisions.jsonl | the journal: every BUY/SELL/SETTLE with full context |
| paper_account.json | the book (atomic writes; audited every pass) |

## The five strategies

1. **Arbitrage** — neg-risk sets and threshold-pair monotonicity violations.
   Payout locked at entry; never sold early; risks zero heat.
2. **Heavy favorites** — backtest-validated 96–98.9¢ band, 24–48h horizon,
   quarter-Kelly sized, every entry gated by microstructure + models.
3. **Explorer** — $1 information bets across 85–99¢ / 1–24h. Its product is
   the resolution label. No stops (stake = insurance), exempt from risk
   models (budget-governed), Thompson-sampled with replay priors.
4. **News reaction** — fades large moves on slow markets. Quarantined behind
   gates earned the hard way (in-game jump markets killed it).
5. **Fast day trading** — own watchlist bulk-priced every 15s; fades 3¢+/5min
   overreactions confirmed by the chart classifier; bracket exits.

## The fifteen models

1 vol regime · 2 equity trend · 3 Bayes per strategy · 4 time-of-day
(per-strategy) · 5 correlation clusters (locked arbs excluded) · 6 market
impact · 7 move z-score · 8 quote quality · 9 edge-gone exit (90¢) ·
10 book-pressure exit · 11 pattern miner (pairs-only vetoes, 14-day window,
info book exempt) · 12 slide exit (OBI-guarded, info book exempt) ·
13 the brain · 14 Thompson explorer · 15 market model (recorder
corpus) · 16 learned chartist (chartml.py: move-reversion model trained
on tick memory's own history; gates the fast fade desk).

**The recurring lesson, encoded**: any model that can block a strategy from
generating the data that would unblock it is a self-locking trap. Risk
models govern profit books; the information book answers to its budget.

## The learning stack

- **The brain (model 13)** — a committee: logistic + GBM variants + forest
  variants + MLP race on 5-fold chronological CV every retrain; positive-
  skill members form a skill-weighted, Platt-calibrated stack. Features
  include auto-engineered miner patterns. Online SGD nudges weights the
  moment each settle lands. Credibility = effective-n × measured OOS skill.
- **Model 15** — trained on thousands of resolved recorder markets, judged
  by Brier against the market price itself. Earned its entry gate when its
  dislikes proved out (10/10 losers).
- **The pattern miner (model 11)** — singles + pairs over trade traits;
  proven losers become entry vetoes; discoveries become brain features.
- **Attribution** — settled P&L bucketed by every decision signal; the
  2-hourly review reads it and ships at most one evidence-gated change.

## Bankroll management

Risk per trade = 1% of current equity. Portfolio heat (Σ worst-case loss)
capped at 10%. Brackets size by risk ÷ stop-distance. Drawdown ladder cuts
sizing 25/50/75% at −2/−4/−6% from the high-water mark. Daily 3% circuit
breaker halts new entries. Monte Carlo VaR/CVaR with intra-cluster
correlation reprices the book every pass. Books audited to the penny.

## Operational

- atomic writes everywhere (crash cannot corrupt state)
- /api/health heartbeat; watchdog restarts dead OR hung bots
- shared API governor (8/s, burst 16, 429 backoff, 30 orders/min)
- `bot.py test` (93 checks) must pass before any restart;
  `tests.py` (80 checks) goes deeper; `ml.py` self-proves on planted
  problems
- commands: paper · brief · attribution · postmortem · research · replay ·
  risk · audit · patterns · promote · mlmodel · sweep-able via config

## The honest scoreboard (as of 2026-06-12)

Trading: last 25 settles ≈ breakeven (−$0.002/trade) vs −$0.49 in era one.
Explorer resolutions: perfect record. ML: committee CV skill ~0.06 (up from
0.026 logistic baseline); m15 within 0.004 Brier of beating the market.
The thesis trade (favorites) still awaits its first true resolutions —
the system's machinery is proven; its edge is still on trial.
