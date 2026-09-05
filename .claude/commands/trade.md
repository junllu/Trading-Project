Run today's agentic trading routine for the campaign to $1,000,000. Discipline
first — capital preservation beats chasing the target ("make no mistake").

Follow these steps in order. Do not skip the guardrails or the approval.

1. **SYNC (live data in):** Using the **robinhood-trading** MCP, read my account
   and all current positions (read-only). Update `config/holdings.yaml` from the
   live data (same as /sync-holdings).

2. **ANALYZE (the brain):** Generate a fresh trade plan from the portal:
   - Run: `.venv\Scripts\python -m app.agent` (Windows) or
     `.venv/bin/python -m app.agent` (macOS/Linux).
   - This writes `data/trade_plan.json`. Read that file — it holds the order
     intents, the campaign status, the guardrails, and the limits.

3. **GUARDRAILS (hard stops):** If `guardrails.drawdown_halt_active` is true OR
   `guardrails.kill_switch` is true → **place NOTHING.** Tell me the campaign is
   in capital-preservation halt, show the drawdown, and stop here.

4. **RECONCILE:** Compare each order in the plan against my live Robinhood
   positions:
   - Skip any SELL for shares I don't actually hold.
   - Skip or shrink any BUY that would breach `limits.max_position_value` or
     `limits.max_order_value`. Respect `limits.max_orders_per_day`.
   - New BUYs only in `limits.focus_symbols`.

5. **PRESENT & ASK:** Show me a clear table — symbol, side, ~shares, $ value,
   conviction, and any guardrail notes — plus the campaign line (progress to $1M,
   days left, required return, drawdown vs halt). Then **ask me to approve.**
   Place nothing without an explicit "yes."

6. **EXECUTE:** On my approval, place the approved orders as **market** orders
   through the robinhood-trading MCP, one at a time, confirming each fill before
   the next.

7. **REPORT:** Summarize every fill (price, shares), my new cash and positions,
   and updated progress toward $1M. Note anything you skipped and why.

Hard rules: never exceed the plan; never invent orders or sizes; never trade
without my approval; honor the halt absolutely.
