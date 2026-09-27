# Pre-registration — entry × exit, trade-level (v4)

Committed before the simulation is run. Changing it after results exist is a
new study with a new trial count.

## What is different from v1–v3

v1–v3 scored a signal by its average forward return at a fixed horizon. That
answers "does the signal carry information", not "is there a profitable way to
trade it". A trade has an entry AND an exit, and the exit changes the win rate,
the size of wins and losses, and how long capital is tied up. The operator's
own record (`app/intel/exit_discipline.py`) shows exits cost more than entries
ever made: ~$112k forgone on three early sales. So this study pairs the
evaluated entries with exit families that include letting winners run.

## Honest limit on cleanliness

All 533 names (312 study + 221 holdout) have now been examined for these
triggers, so no backtest here is fully out-of-sample. The CONTROL (below) and a
two-era consistency requirement limit self-deception; the forward paper test
from 2026-09-28 is the real validation for anything that passes.

## Entries (fixed; defined in `breakout_study.py` / `breakout_regime_study.py`)

| id | signal | idea |
|---|---|---|
| E1 | bo55 + volume + close | momentum breakout (best of v1) |
| E2 | bd55 + relstr(weak) + market(down) | capitulation bounce (best of v3) |
| E3 | bd20 | plain dip |
| E0 | CONTROL: every name, one entry every 20 sessions, no signal | what the exit alone does |

Signal known at the close; entry at the NEXT open. One open position per name
per combo (signals while in a trade are skipped).

## Exits (conventional parameters, NOT swept)

| id | rule |
|---|---|
| X1 | time: exit at the close 5 sessions after entry |
| X2 | time: exit at the close 20 sessions after entry |
| X3 | bracket: stop entry − 2·ATR, target entry + 3·ATR, time stop 20 |
| X4 | trailing: stop = highest close − 3·ATR (ratchets up only), time stop 120 |
| X5 | trend: exit next open after a close below the prior 20-session low, time stop 120 |

ATR = 14-session ATR as of the signal bar. Intrabar: a stop fills at
min(open, stop) (gaps fill worse); a target at max(open, target); if one bar
touches both, the STOP is assumed first. Close-based exits fill at the next
open (you cannot trade the close you just observed). Cost: retail round trip,
also reported at 3×.

## Metrics per combo

Trades, win rate, average win, average loss, payoff ratio, **expectancy**
(net % per trade), profit factor, median hold, expectancy per day held,
longest losing streak, and EDGE = expectancy − the control's expectancy with
the same exit, measured month by month (trades grouped by entry month).

## Trials and pass rule

3 entries × 5 exits = **15 trials** (the control is the benchmark, not a
hypothesis). A combo passes if ALL of:
1. expectancy net of cost > 0, and > 0 at 3× cost;
2. EDGE vs control > 0 with **t ≥ 2.71** (one-sided 5%, Bonferroni for 15),
   t computed on the monthly edge series;
3. EDGE > 0 in both eras separately (≤ 2022 and ≥ 2023).

Passing combos go to the forward paper test. The scorecard for every combo is
reported whether or not it passes — that is the "success rate" monitor.
