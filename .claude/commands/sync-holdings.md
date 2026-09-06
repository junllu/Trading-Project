Pull my current holdings from Robinhood (live) and update the portal's data so
the dashboard, conviction engine, and campaign all reflect my real book.

Do this:

1. Using the **robinhood-trading** MCP, fetch my account and ALL current stock
   positions. **Read only — place, modify, or cancel NO orders.**
2. For each position collect: ticker symbol, share quantity, average cost
   (cost basis per share), and current/last price. Also note total account value
   and available cash.
3. Overwrite `config/holdings.yaml` with exactly this shape — a top-level `cash:`
   set to my **real uninvested cash / buying power** from the account, then one
   line per position (`broker: robinhood`):

   ```yaml
   cash: 1234.56
   holdings:
     - {symbol: MRVL, shares: 88, avg_price: 256.98, last: 222.69, broker: robinhood}
   ```

   The `cash:` line is important: without it the portal shows $0 real cash. Never
   invent a cash figure — use the real balance from the MCP.

4. Print a short table of what you wrote (symbol, shares, avg, last, market value),
   the real cash, and the total account value (cash + positions), and confirm it
   matches my Robinhood app. This total is what the campaign tracks toward $1M.

If the portal server is running, remind me it will pick up the new holdings on
its next launch (or I can just restart it).
