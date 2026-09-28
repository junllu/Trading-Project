# Pre-registration — sell index puts after a fear spike (VIX-spike put-write)

Committed before the run. Implemented by `app/options/vix_putwrite.py`.

## The observation (user, 2026-09-28)

On a risk-off day option prices jump. The hypothesis: implied volatility
overshoots the move that follows, so selling puts *right after* a fear spike
earns more than selling on a schedule.

## Why the index

No historical single-stock option prices exist here, but the VIX is the S&P
500's 30-day implied volatility, with daily history since 1990 — a real,
contemporaneous option price, not an assumption.

## Mechanics (fixed)

- Sell a 21-session (≈30-day) S&P put at the close, strike at Black-Scholes
  |delta| 0.25, priced with IV = VIX/100 (rate 4.5%). Payout = max(K − S_T, 0)
  at the close 21 sessions later. SPY stands in for the index (adjusted closes).
- P&L per trade = (premium − payout) / strike (return on cash collateral).
- CONSERVATIVE premium: OTM S&P puts trade ABOVE ATM implied vol (skew), so
  pricing them at the VIX understates what a seller actually collects.
- One position at a time (no overlap); entries only on signal days.
- Window: 1993-2026 (all SPY/VIX history cached). Eras: < 2008, 2008-2019, 2020+.

## Configurations (3 trials) vs the always-on baseline

| id | enter when |
|---|---|
| BASE | every 21 sessions, no condition (the classic monthly put-write) |
| V1 | VIX close ≥ 1.20 × its 20-session average (a spike) |
| V2 | VIX / SPY trailing 20-session realised vol ≥ 1.5 (fear priced above movement) |
| V3 | V1 and V2 together |

## Pass rule (a V-config passes if all hold)

1. Mean P&L per trade > BASE's, Welch one-sided t ≥ 2.13 (5%, Bonferroni 3).
2. Mean P&L per trade > 0 in all three eras.
3. 5% CVaR per trade no worse than BASE's by more than half (tails controlled).

A pass becomes a paper track on XSP/SPY options with real quotes.
