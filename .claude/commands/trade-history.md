Extract my full Robinhood trade history and analyze it.

1. Using the **robinhood-trading** MCP, fetch ALL my historical orders — every
   filled buy and sell, as far back as the API allows. Read only; place nothing.
2. Write them to `data/trade_history.json` in exactly this shape (one entry per
   FILLED order; use the execution date as YYYY-MM-DD):

   ```json
   {"orders": [
     {"symbol": "TLRY", "side": "buy",  "quantity": 100, "price": 50.00, "date": "2021-02-10"},
     {"symbol": "TLRY", "side": "sell", "quantity": 100, "price": 20.00, "date": "2022-08-01"}
   ]}
   ```
   Include partial fills as separate entries. Skip cancelled/pending orders.

3. Run the analyzer:
   ```
   .venv\Scripts\python -m app.portfolio
   ```
   It matches buys to sells (FIFO), and reports realized P&L, win rate, average
   win/loss, holding periods, per-symbol performance, and **late exits** — trips
   where I sold well after the policy regime for that sector had already turned
   negative (the "exited too late" pattern, e.g. cannabis after 2021).

4. Summarize for me: total realized P&L, win rate, my best and worst names, and
   especially the late-exit flags — what the macro layer says I held too long.
