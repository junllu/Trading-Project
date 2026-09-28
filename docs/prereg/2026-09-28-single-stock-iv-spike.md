# Pre-registration — single-stock IV spike: sell puts / covered calls (pilot)

Committed before any option history is harvested. Implemented by
`app/options/iv_backtest.py`; data harvested read-only from Robinhood's
option historicals (expired contracts) into `data/option_history/`.

## Question

The user's observation (2026-09-28): on risk-off days single-stock option
prices jump. Does selling right AFTER a contract's implied volatility spikes
capture more premium, net of realistic costs, than selling on a schedule?
(The index version, vix1, nearly passed; this tests single stocks.)

## Data

- Panel (17): GOOGL MRVL NVDA META TSLA NOW SPCX AAPL MSFT AMZN AMD AVGO MU
  NFLX PLTR COIN JPM. SPCX (listed 2026-06) contributes only its few expiries.
- Expiries: the monthly (third-Friday) expiry of each month 2025-10 → 2026-09
  (12). Per symbol × expiry × type, the 3 listed strikes nearest a 0.25-delta
  target computed from the stock's trailing 20-session realised vol at the
  reference date (expiry − 30 calendar days). Daily bars from 45 days before
  expiry to expiry.
- Implied vol per contract per day by Black-Scholes inversion of the option's
  daily close against the stock's close (rate 4.5%, no dividends). A bar
  flagged interpolated is not a trade and is skipped.

## Trades (per symbol × expiry × type, contract = the one of the 3 whose
## entry-day |delta| is closest to 0.25)

- BASE: sell at the close on the reference date (or the next day with a real bar).
- SPIKE: sell at the close on the FIRST day in [expiry − 40d, expiry − 21d]
  where the contract's IV >= 1.20 × its own trailing 10-bar mean IV AND
  IV >= 1.20 × the stock's trailing 20-session realised vol. No such day ⇒ no trade.
- Both held to expiry, settled on the stock's expiry close.
- Fill haircut: premium × (1 − 0.075) (history has no bid/ask); every result
  also reported at 0.15.
- P&L: puts = (premium − max(K − S_T, 0)) / K; covered-call leg =
  (premium − max(S_T − K, 0)) / S_entry (the stock leg is identical in both
  arms, so the comparison is the call leg). Percent.

## Two trials and the pass rule

PUT and CALL, each passes if ALL hold:
1. Paired difference SPIKE − BASE (same symbol × expiry, both traded) > 0,
   t ≥ 2.24 (5% one-sided, Bonferroni for 2), with t computed on per-EXPIRY
   mean differences (same-month trades share one market; counting them
   separately would overstate the evidence).
2. SPIKE mean P&L > 0 at the 0.15 haircut.
3. SPIKE 5% CVaR no worse than BASE's by more than half.

Reported, not tested (no pass/fail, too few samples to claim): long-call
entries when IV is LOW (IV ≤ 0.85 × its trailing mean) vs BASE timing.

A pass ⇒ a forward paper track ("ivspike") on live quotes with real bid/ask.
A pilot fail at 12 months ⇒ extend to 24 months only if the point estimate
favours SPIKE; otherwise the idea is recorded as not supported.
