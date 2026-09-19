Harvest live minute bars for the campaign's focus names into the local store,
then stop. This is a scheduled, unattended job. It is read-only market data plus
one append to a local cache. It places no orders and runs no trading routine.

## Why this job exists

The portal cannot fetch these itself. `app/data/minute.py:fetch()` uses the
Webull OpenAPI SDK, and that key returns **403 — no OpenAPI market-data
subscription** (an Advanced Quotes subscription bought in the Webull app or
desktop does NOT apply to OpenAPI). The claude.ai Webull connector *can* read
them, at `delay_minutes: 0`. So the only path to fresh intraday bars runs
through a Claude session, which is why this job exists rather than an in-process
portal loop.

Everything downstream that reasons about intraday price action — the exit
monitor's true-touch stop check, `app/analytics/intraday.py`'s MAE/MFE work,
candlestick detection — reads this cache. If it goes stale, those answer with
old prices while looking like they answered with current ones.

## Steps

1. Get the symbol batches. The list is derived from the live book, so it follows
   the portfolio instead of going stale in this file:

       .venv\Scripts\python.exe -m app.data.harvest_list

   That prints `BATCH n: [...]` lines of at most 20 symbols each — 20 is the
   connector's per-query ceiling.

2. Call `mcp__claude_ai_Webull__get_stock_bars` **once per batch**, passing that
   batch's symbols verbatim, with:
   - `category`: `US_STOCK`
   - `timespan`: `M1`
   - `count`: `240`
   - `trading_sessions`: `RTH`
   - `real_time_required`: `true`

   240 one-minute bars per symbol overlaps the previous run generously. Ingest
   is idempotent on timestamp, so overlap is the intended design, not waste —
   it is what closes gaps when a run is missed.

   Coverage is the point. A symbol without a minute bar falls back to the last
   daily close, and on 2026-09-08 that fallback was wrong by 9.6% on BE and 4.1%
   on HOOD. A fill priced off a stale close measures staleness, not strategy.

3. Each result is large and the harness will save it to a file rather than
   returning it inline. **Do not read those files into context** — they are
   hundreds of kilobytes of numbers and you do not need to see them. Pass each
   path straight to the ingester, one call per saved file:

       .venv\Scripts\python.exe scripts\ingest_minute_bars.py "<saved file path>"

   If a result did come back inline, write it verbatim to a temp `.json` and
   pass that path instead.

4. Report a SUMMARY, not a per-symbol dump: how many symbols were ingested, the
   total bars added, and the newest timestamp. Name only the symbols that
   returned nothing or were rejected.

5. If `delay_minutes` is greater than 0 on any symbol, say so explicitly. A
   delayed feed presented as real time is worse than a feed known to be late.

6. If a batch call fails, ingest the batches that succeeded and report which one
   failed. A partial harvest is useful; abandoning four good batches because the
   fifth errored is not.

## Hard limits

- Place, modify or cancel NO orders. Read market data only.
- Do not run `app.agent`, the daily agent, the exit monitor, or anything that
  routes signals to an executor.
- Do not edit code, config, or `.env`. The only thing you write is the minute
  cache under `data/minute/`, via the ingester above.
- If the connector errors or returns no rows, report it and stop. Do not fall
  back to the SDK path — it is known-403 and retrying it only wastes the run.
