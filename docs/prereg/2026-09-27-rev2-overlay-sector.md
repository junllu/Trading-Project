# Pre-registration — rev2: index overlay, faster exit, sector rotation

Committed before the run. Implemented by `app/backtest/rev2_sim.py`.

## Why

The rev1 portfolio sim (commit a3bf520) did not beat SPY: S2 earned ~18.8%
per unit deployed but sat 41% in cash at 0%. S1 failed at portfolio level
(0.2% CAGR, 35% max DD) and is excluded from rev2. The user asked for sector
rotation to be weighted into screening; sentiment has no history and is
recorded forward instead (not part of this test).

## Configurations (4 trials), all S2 (bd20) entries

| id | idle capital | S2 exit | sector filter |
|---|---|---|---|
| A | SPY | X3 (−2/+3 ATR, 20) | none |
| B | SPY | X6: exit next open after the first close above its 5-session SMA; time stop 10 | none |
| C | SPY | X3 | buy only if the stock's sector ETF ranks in the top 5 of 11 by 126-session return at the signal close (no sector / no ETF yet ⇒ excluded) |
| D | top-3 sector ETFs by 126-session return, equal weight, re-chosen every 21 sessions at the close, held from the next session | X3 | none |

Everything else as `portfolio_sim.py`: $100k, 2% of prior-close equity per
position, max 40, seeded random selection when signals exceed slots, one
position per name, retail round-trip cost per trade, plus a one-way cost on
every move into or out of the idle asset (and on rotation turnover).
Entries are funded by selling idle capital; exit proceeds return to it.
Window 2016-01-04 → latest, eras split at 2023-01-01. Universe: the 533 names.

## Pass rule

Against SPY buy-and-hold over the same window, a config passes if:
1. CAGR > SPY **and** Sharpe > SPY — full period **and** each era;
2. full-period max drawdown ≤ SPY's.

A pass creates a rev2 paper track alongside rev1 (rev1 is not edited).
