Pull my current holdings from Robinhood (live) and update the portal's data so
the dashboard, conviction engine, and campaign all reflect my real book.

Do this:

1. Using the **robinhood-trading** MCP, fetch my account and ALL current stock
   positions. **Read only — place, modify, or cancel NO orders.**
2. For each position collect: ticker symbol, share quantity, average cost
   (cost basis per share), and current/last price. Also note total account value
   and available cash.
3. Overwrite `config/holdings.yaml` with exactly this shape (one line per
   position; `broker: robinhood`):

   ```yaml
   holdings:
     - {symbol: MRVL, shares: 88, avg_price: 256.98, last: 222.69, broker: robinhood}
   ```

4. Print a short table of what you wrote (symbol, shares, avg, last, market value)
   and the total book value, and confirm it matches my Robinhood app.

If the portal server is running, remind me it will pick up the new holdings on
its next launch (or I can just restart it).
