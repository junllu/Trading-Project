# Confidence-gated capital sleeve

Scoreboard: **% return** and **edge vs buy-and-hold** on forward-recorded signals.
Not distance to $1M.

## Ladder

| Gate | Graded signals | Extra | Live sleeve | Paper shadow |
|------|----------------|-------|-------------|--------------|
| NO DATA | < 30 | — | $0 | $0 |
| MEASURED | ≥ 30 | — | $0 | $1,000 |
| EVIDENCED | ≥ 100 | edge vs hold > 0 | $1,000 | $1,000 |
| ESTABLISHED | ≥ 250 | edge > 0 and sleeve maxDD ≤ 25% | $5,000 | $5,000 |

If edge ≤ 0 at any time → live sleeve forced to **$0**.

Promotion of a strategy release (`python -m app.backtest.release promote …`) stays **manual**.
`sleeve` only prints a stage hint (`backtest_only` / `paper` / `live_confirm`). It never suggests `live_auto`.

## Daily loop

1. Keep the portal live loop on (record-only intents).
2. `python -m app.analytics.confidence`
3. `python -m app.analytics.sleeve` — see gate + recommended $ 
4. When edge > 0: `python -m app.analytics.sleeve --reward` → deep_dive winners/losers
5. When gate hits EVIDENCED: fund **$1k** in the agentic sleeve, `release promote … live_confirm`
6. When ESTABLISHED + DD ok: scale to **$5k**

## Rewards

Successful edge unlocks **deeper analysis**, not more size:
- `python -m app.analytics.deep_dive SYMBOL`
- calibration fit

## Dashboard

Card **Capital Sleeve** on the portal. Cached via GET /api/sleeve; click **Refresh grade** for POST /api/sleeve/refresh.



## Related integrity (build order)

1. **Next-bar fills** in `app/backtest/engine.py` — signal on close[t], fill on close[t+1].
2. **Sleeve-gated buys** in `app/analytics/trade_filters.py` — live buys need recommended_sleeve_usd > 0.
3. **Theme gate** — no new buys into DEAD / ROTATING_OUT / NO_DATA themes.
4. **Re-entry alerts** — attached on `trade_plan.json` under `plays.reentry_alerts`.


## Staircase exits & reentry

On each order's `exit_plan.staircase`:

- **Scale-out** into strength (default thirds at +10% / +20% / +35% from cost).
- **Scale-in** (down-average) at −8% / −16%, max 2 adds, each ≤ 25% of original —
  only if theme is ALIVE/FADING **and** portfolio gate passes (book DD ≤ 10% from
  peak, or sleeve edge vs hold > 0).

Mistakes are allowed as *budgeted sleeve learning*, not unlimited averaging.
