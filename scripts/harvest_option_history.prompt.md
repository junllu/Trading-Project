You are harvesting HISTORICAL option prices for a research backtest. Read-only: use ONLY the
Robinhood get_option_instruments and get_option_historicals tools, plus Read/Write/Bash for files.
Never place, cancel or modify anything.

Work in C:\Users\winter\Documents\GitHub\Trading-Project.

1. Read data/option_history/plan.json. A task is DONE if data/option_history/<symbol>_<expiry>.json exists.
2. Take the first {MAX_TASKS} not-done tasks (exactly {MAX_TASKS}; fewer only if fewer remain). For each:
   a. mcp__robinhood-trading__get_option_instruments(chain_symbol=<symbol>, expiration_dates=<expiry>,
      type="put", state="expired") and the same with type="call". If a result has a `next` cursor, fetch
      the remaining pages.
   b. Pick the 3 put strikes nearest put_target and the 3 call strikes nearest call_target.
   c. mcp__robinhood-trading__get_option_historicals(instrument_ids=[those <=6 ids],
      start_time="<window_start>T00:00:00Z", end_time="<expiry + 1 day>T00:00:00Z", interval="day").
   d. Write data/option_history/<symbol>_<expiry>.json exactly as:
      {"symbol": "...", "expiry": "YYYY-MM-DD", "contracts": [
        {"type": "put"|"call", "strike": <number>, "id": "<instrument id>",
         "bars": [{"date": "<begins_at first 10 chars>", "close": <close_price as number>,
                   "interpolated": <true|false>}, ...]}]}
      Write it with a short python snippet via Bash (json.dump) so numbers stay numbers.
   e. If a symbol/expiry has no expired contracts (e.g. listed later), write the file with
      "contracts": [] so it is not retried.
3. Reply with one line: "harvested N, remaining M" (run `.venv\Scripts\python -m app.options.iv_backtest status`).

This is an unattended run: there is no user to answer questions. Never ask; just do the batch.
