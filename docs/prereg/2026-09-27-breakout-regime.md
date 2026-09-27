# Pre-registration — breakout/breakdown × confirmation × regime (v2)

Written and committed **before** any 2023+ result or any new-universe price
was examined. The git commit timestamp is the proof of ordering. Any change to
this document after results exist is a new study with a new trial count.

Decided with the user 2026-09-27. Prior study (v1, 264 trials, 90 names,
discovery 2015–2022) found no survivors; those 264 trials stay in the registry.

## Universe (`2026-09-27-breakout-regime-universe.json`, seed 20260927)

- **Study universe:** the 92 names already cached + 220 of the S&P 500 names
  not previously cached, chosen at random = 312 names.
- **Holdout names:** the other 221 S&P 500 names. Not examined until the final
  step, and then only for combos that already passed both eras.
- Known bias, accepted: S&P 500 membership is as of 2026-09-27 (survivorship).
  Measured as excess over the same-day universe average, which shares the
  bias, but breakout names may survive more often than average.

## Grid (fixed)

- **Triggers (4):** bo20, bo55 (fresh close above prior N-day high),
  bd20, bd55 (fresh close below prior N-day low). Breakdowns scored
  direction −1 (success = underperformance; used as exit/avoid).
- **Conditions (10)**, each read from data knowable at that day's close.
  "Agrees" = risk-on side for breakouts, risk-off side for breakdowns:
  - volume: rvol ≥ 1.5 (both)
  - trend: close vs 200dma agrees
  - relstr: 63d relative-strength rank ≥ .7 (bd ≤ .3)
  - squeeze: prior-day vol rank ≤ .3 (both)
  - market: SPY vs its 200dma agrees
  - close: close in top (bd bottom) quarter of the bar's range
  - vix: VIX below (bd above) its trailing 252-session median
  - credit: HYG/IEF ratio above (bd below) its value 63 sessions earlier
  - rates: 10y yield (DGS10, prior day, publication lag) below (bd above) its
    value 63 sessions earlier
  - cycle: presidential-cycle year 3 (bd: not year 3) — the one macro signal
    that previously survived measurement
  - (HY OAS was intended; FRED licenses only 3 years of it, so HYG/IEF
    replaces it for full 2015+ coverage.)
- **Combos:** each trigger alone, with each single condition, with each pair
  = 1 + 10 + 45 = 56 per trigger × 4 triggers × horizons {5, 10, 20}
  = **672 trials**, all recorded to `data/trials.jsonl`.

## Measurement (unchanged from v1)

Entry next open, exit close at h; excess over the same-day, same-horizon
average of the universe being tested; direction-signed; net of retail round
trip cost (also reported at 3×); t-stat on non-overlapping h-session blocks.

## Pass rule (all three, in order)

1. **Era A, current regime: 2023-01-01 → latest.** blocks ≥ 30, mean net > 0,
   t ≥ E[max of 672 null t-stats] (computed by `trials.expected_max_sharpe`).
2. **Era B: 2015 → 2022-12-31.** same combo, blocks ≥ 30, mean net > 0,
   t ≥ 2.0. (Era B was partly examined in v1 for the 6 original conditions;
   the rule is mechanical and fixed here, so that knowledge cannot steer it.)
3. **Holdout names, full period:** mean net > 0, t ≥ 2.0.

Only combos passing all three go to the forward paper test. None passing is
a valid result and will be reported as such.
