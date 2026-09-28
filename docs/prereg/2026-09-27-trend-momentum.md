# Pre-registration — trend-timed index and point-in-time mega-cap momentum

Committed before the run. Implemented by `app/backtest/trend_momentum.py`.
Context: nine tests found daily price signals across hundreds of names lose to
a mega-cap-led index. These two work WITH that market and have long external
evidence (Faber 2007 timing model; Jegadeesh-Titman 12-1 momentum).

## Configurations (4 trials)

| id | rule |
|---|---|
| T-SPY | Month-end: SPY close above its 200-session average ⇒ hold SPY next month, else 3-month T-bills (DTB3, prior-day print) |
| T-QQQ | Same rule on QQQ |
| M-50 | Every 21 sessions: from the 50 names with the highest trailing 63-session average dollar volume (price × volume — size proxy KNOWN AT THE TIME), hold the 10 with the best return from t−252 to t−21, equal weight |
| M-100 | Same from the top 100 by dollar volume |

Momentum universe: the 533 names (current S&P list — survivorship remains, but
size is point-in-time, not today's ranking). Returns close-to-close; a
decision at a close applies from the next session. Costs: retail one-way
cost on every dollar traded (switches, rebalance turnover). Window
2016-01-04 → latest; eras split 2023-01-01.

## Pass rule

Primary (risk-adjusted alpha): Sharpe > SPY's in BOTH eras. Reported alongside:
CAGR and max drawdown vs SPY, and whether the average return is positive in
both eras (the user's bar for strategies generally).
