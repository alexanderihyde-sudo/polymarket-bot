# Polymarket Paper-Trading Bot

A bot that watches real, live Polymarket prices and trades **simulated money**
using two real strategies. After it builds a track record, you'll know — with
actual numbers instead of hope — whether it's worth putting real dollars in.

## The honest part (read this once)

- **Nobody can promise you returns.** Any bot, course, or video that does is
  lying. The strategies here are real and documented, but the profitable
  moments are rare because professional bots compete for them.
- **Paper trade first, always.** Run this for at least 3–4 weeks. If the
  "realized profit" number is positive across 20+ settled trades, *then* it's
  worth a conversation about real money. If it's negative, the bot just saved
  you that money.
- **Never share your crypto wallet's private key** with any website, app, or
  person. This bot doesn't need it or ask for it.
- Check that trading on Polymarket is allowed where you live before going live.

## How to run it

Open the Terminal app, then:

```
cd ~/polymarket-bot
python3 bot.py paper     # start the bot + dashboard (leave the window open; Ctrl+C stops it)
```

Your browser opens **http://localhost:8765** automatically — a live dashboard
showing your account value over time, open trades, full trade history, an
activity feed, and what the bot has learned. It updates by itself every few
seconds. ("localhost" means the page is served by the bot on your own Mac —
nothing is on the internet.)

Other commands:

```
python3 bot.py web       # view the dashboard without trading
python3 bot.py scan      # look for opportunities right now, trade nothing
python3 bot.py status    # account summary in the terminal
python3 bot.py backtest  # replay the strategy against real resolved markets
python3 bot.py reset     # wipe the pretend account, start fresh
python3 bot.py brief     # compact one-screen digest (made for AI/quant reviews)
python3 bot.py risk      # Monte Carlo VaR/CVaR + stress test of the open book
```

`backtest` is the quant move: instead of waiting weeks for live results, it
pulls hundreds of already-resolved markets, looks up what the favorite
actually traded at 48h/24h/6h before resolution, and reports the win rate
and return per price band. Results appear on the dashboard and inform which
price ranges the bot should trade. Re-run it monthly — markets change.

## The research recorder (collecting data at scale)

While `paper` runs, a recorder logs ~400 markets' live prices **every
minute** into the `data/` folder — this is how trading desks build datasets,
and it's far more information than any number of individual trades. Run
`python3 bot.py research` every few days: it matches recorded prices with
real outcomes as markets resolve and reports win rates and returns per price
band and time-to-resolution — thousands of simulated trades from your own
recorded data. (Repeated snapshots of the same market are deduplicated so
the statistics stay honest.) Raw files use ~25 MB/day; only the last 30 days
are kept.

The bot starts with $100 of pretend money. Every scan it:
1. Checks if any markets it "bought" have resolved, and collects the payout.
2. Hunts for new opportunities and buys them with pretend money.
3. Saves everything to `paper_account.json` and `trade_log.csv`
   (the CSV opens in Excel/Numbers — your full trading history).

"No opportunities this pass" is the most common message. That's the bot being
picky, which is exactly what you want.

## The two strategies, in plain English

**1. Arbitrage (`arbitrage`)** — For an event where exactly one outcome must
win (an election, a championship), if you can buy YES on *every* outcome for
a combined total under $1.00, you're guaranteed profit — one of them must pay
out $1. This is real, risk-free math. The catch: prices usually sum to just
*over* $1 (right now: 1.003–1.005 on the big events), and the bot waits for
the rare dips below. Expect a few catches per week at best, each worth cents.

**2. Heavy favorites (`high_prob`)** — Research on prediction markets shows
people overpay for longshots, which means favorites are slightly *under*priced
on average. The bot buys outcomes priced 96–99¢ that resolve within days.
It wins pennies most of the time and takes a hit when an upset happens.
Whether the pennies beat the upsets is exactly what the paper account will
tell you.

While a favorite is held, the bot re-checks its live price every minute and
sells if it falls to 85¢ (cutting the loss instead of riding a collapsing
favorite to zero) or if it reaches 99.5¢ (locking the win early and freeing
the cash). Honest trade-off: a stop-loss also sells on temporary dips that
would have recovered — whether it helps or hurts overall is another thing
the paper results will show. Arbitrage positions are never sold early; their
payout is only guaranteed if held to resolution.

## The quant layer

Every candidate trade passes through the math real desks use:

- **Kelly criterion sizing** — bet size is computed from the estimated edge:
  pooled win rates (backtest + your own settled trades) shrunk to a
  conservative statistical lower bound (Wilson score), then quarter-Kelly.
  If the evidence doesn't support an edge at the offered price, the trade is
  skipped entirely — this is why the bot may refuse whole price bands.
- **Spread filter** — skips books where the bid-ask spread is wide enough to
  eat the edge in slippage.
- **Order-book imbalance** — skips when resting orders near the price are
  heavily sellers (downward pressure).
- **Momentum filter** — checks the last 6h of price history; never buys a
  favorite that is actively collapsing.
- **Entry window** — only 6h–48h before resolution, the horizon the backtest
  validated. (Closer entries tested weak; 15-minute crypto markets are out.)
- **Performance metrics** on the dashboard: win rate, expectancy per trade,
  profit factor, max drawdown, annualized Sharpe ratio (`python3 bot.py
  stats` prints the same in Terminal).

A note on stop-losses from real experience: prices can gap. A collapsing
favorite may blow straight through the 85¢ stop and fill far lower. Stops cut
disasters; they don't guarantee the stop price.

## How the bot "learns"

This is a feedback loop based on real results, not magic AI (with only dozens
of trades, real machine learning would just fool itself on noise):

- Nothing changes until a strategy has **8 settled trades** — adapting on
  less data would be guessing.
- If the last 8 settled trades of a strategy lost money overall, its trade
  size is **cut in half**.
- If the last 16 lost money overall, the strategy is **paused** entirely.
- For heavy favorites, each entry price (96¢, 97¢, 98¢, 99¢) is tracked
  separately. A price range with 6+ settled trades and a net loss **stops
  being bought** — e.g. if 98¢ favorites keep getting upset, the bot learns
  to avoid 98¢ while still buying 97¢.
- The same is done per **market type** (Sports, Crypto, Politics, ...): a
  type with 6+ settled trades and a net loss stops being traded. If sports
  favorites win but crypto favorites keep losing, it drops crypto.
- Every adjustment is announced in the dashboard's activity feed and shown
  in the "What the bot has learned" panel, so you always know why it's
  behaving the way it is.

Improving results also re-enable things: the rules are recomputed from the
trade history on every scan.

## Settings (config.json)

| Setting | Means | Default |
|---|---|---|
| `starting_cash` | Pretend bankroll | $100 |
| `scan_interval_minutes` | How often it hunts for new trades | 3 |
| `monitor_interval_seconds` | How often it re-checks prices on open trades | 10 |
| `settle_check_seconds` | How often it checks if held markets resolved | 60 |
| `exit.stop_loss_price` | Sell a favorite if its price falls to this (cut the loss) | 0.85 |
| `exit.take_profit_price` | Sell early if the price reaches this (lock the win) | 0.995 |
| `arbitrage.min_edge_cents` | Min locked profit per $1 to act | 1.5¢ |
| `arbitrage.max_cost_per_arb` | Max spent on one arbitrage | $10 |
| `high_probability.buy_price_min/max` | Favorite price range to buy | 0.96–0.99 |
| `high_probability.max_days_to_resolution` | Only markets ending soon | 4 days |
| `high_probability.max_dollars_per_trade` | Max per favorite | $5 |
| `high_probability.max_open_positions` | Max favorites held at once | 10 |

`python3 bot.py reset` wipes the pretend account for a fresh start.

## Going live later (not yet)

When you have 3–4 weeks of paper results, the path is: review the numbers →
if (and only if) they're positive, add Polymarket's official order-signing
library (`py-clob-client`) with strict spending caps → start with $20–50, not
more. Ask Claude to build that step when your paper results justify it.

## Files

- `bot.py` — the bot
- `dashboard.html` — the web dashboard it serves at http://localhost:8765
- `config.json` — your settings
- `paper_account.json`, `trade_log.csv`, `history.json`, `learning.json`,
  `activity.log` — created as it runs (your results and what it has learned)
- `old_bot_backup.py` — your previous attempt, kept for reference (its "edge"
  formula on line 80 assumed every market was mispriced by 5.5¢ — don't use it)

## The 10-model overlay (added Jun 11)

Ten lightweight models vote on every check, on top of the strategies:
sizing throttles (volatility regime, equity trend, Bayesian confidence,
time-of-day blocks, correlation-cluster caps), entry gates (market impact,
move z-score, thin-book quality), and live exit models (edge-gone, book
pressure). State lives in models_state.json and on the dashboard's Models
tab; how often each acted is counted so they can be judged on evidence.

## Risk analytics

Every scan also writes risk_report.json: 4,000-path Monte Carlo of the open
book (positions in the same cluster fail together), reporting expected P&L,
95% VaR/CVaR, the absolute worst case, and per-cluster stress scenarios.
Shown on the dashboard stat strip and Research tab.

## The dashboard (TradingView-style)

http://localhost:8765 — terminal-dark equity chart (green above your $1,000
start, red below) with crosshair + price axis, range buttons (1H/6H/24H/ALL),
a watchlist sidebar of strategy/category sub-accounts (click to filter
everything), a stat strip (cash, VaR, size multiplier...), and bottom tabs:
Positions / Settled / Learning / Models / Strategy / Research / Activity.
Shortcuts: "/" search, Esc clear, 1-7 switch tabs.
