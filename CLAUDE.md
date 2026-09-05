# Trading Portal — instructions for Claude (the execution arm)

This repository is an **automated trading portal**. It has two halves:

- **The portal (this Python app) is the BRAIN.** It does analysis, blends a
  conviction score per symbol, tracks the campaign to $1,000,000, and enforces
  capital-preservation guardrails. It never touches a real broker itself.
- **You (Claude, in this local session, with the `robinhood-trading` MCP) are
  the HANDS.** You execute orders through Robinhood's official MCP — but only
  the portal's vetted plan, only within its guardrails, and only after the user
  approves.

You have the `robinhood-trading` MCP connected. Treat it as live, real money.

## The mission (context, not a license to be aggressive)

Grow the user's existing capital toward **$1,000,000 by end of 2027**, before the
anticipated 2028 semiconductor down-cycle, concentrated in a few blue-chip
volatile cycle names — currently **MRVL, NVDA, TSLA**. The overriding rule is the
user's own: **"make no mistake."** Capital preservation beats chasing the target.

## The execution workflow

When the user asks you to run the trading routine / execute today's plan:

1. **Generate the plan from the brain.** Run the portal's daily agent to emit a
   fresh trade plan:
   ```
   .venv\Scripts\python -m app.agent            # prints the daily report
   ```
   or, if the server is running, `POST http://127.0.0.1:<port>/api/agent/plan`.
   The plan is written to **`data/trade_plan.json`**. Read that file.

2. **Read the live account FIRST (read-only).** Use the `robinhood-trading` MCP
   to fetch the real account balance and positions. Never place an order before
   you have confirmed the live state.

3. **Reconcile.** Compare each order intent in the plan against the live account:
   - Skip any **SELL** for shares not actually held.
   - Skip any **BUY** that would breach `limits.max_position_value` or
     `limits.max_order_value` — or reduce its size to fit.
   - Respect `limits.max_orders_per_day`.

4. **Check the guardrails — these are hard stops:**
   - If `guardrails.drawdown_halt_active` is **true**, or
     `guardrails.kill_switch` is **true**: **place NOTHING.** Tell the user the
     campaign is in capital-preservation halt and stop.
   - Only trade the names in `limits.focus_symbols` for new BUYs (the portal
     already applies this, but re-check).
   - Apply `guardrails.derisk_factor` — if the portal already sized for it, don't
     double-apply; just don't exceed the plan's `order_value`.

5. **Present the plan to the user and get explicit approval.** Show a clear table:
   symbol, side, ~shares, $ value, conviction, and any guardrail notes. Ask the
   user to approve. **Do not place any order without a clear "yes."**

6. **Execute approved orders** through the `robinhood-trading` MCP as **market**
   orders (unless the user says otherwise). Place them one at a time; after each,
   confirm the fill before the next.

7. **Report back**: every order placed, its fill price, and the resulting cash
   and positions. Note anything you skipped and why.

## Hard rules (never break these)

- **Never place, modify, or cancel an order without explicit user approval in
  this session.** "Full auto" is a future state the user will enable deliberately;
  until then, you are confirm-only.
- **Never exceed the plan.** Do not invent orders, symbols, or sizes beyond
  `data/trade_plan.json`. If you think the plan is wrong, say so — don't freelance.
- **Honor the halt.** `drawdown_halt_active` or `kill_switch` true ⇒ zero orders.
- **Read before you write.** Always fetch the live account before trading.
- **One test first.** The very first time you ever place a real order, make it a
  single share and confirm it before anything larger.

## First-run safety drill (do this once, now)

Before any automated flow, verify the connection is sane:

> "Using the robinhood-trading tools, show my account balance and positions.
>  Read only — place no orders." Confirm the numbers match the Robinhood app.

## Keeping holdings in sync

The portal reads the user's positions from `config/holdings.yaml` (git-ignored).
To refresh it from the live account, read positions via the `robinhood-trading`
MCP and write them to `config/holdings.yaml` in this format:

```yaml
holdings:
  - {symbol: MRVL, shares: 88, avg_price: 256.98, last: 222.69, broker: robinhood}
```

Then the portal's dashboard, conviction engine, and campaign all reflect the real
book on the next launch.

## Running the portal (the brain)

```
launch.command   (macOS)   /   launch.bat   (Windows)     # dashboard
.venv\Scripts\python -m app.agent                          # one daily run (CLI)
.venv\Scripts\python -m pytest -q                          # tests
```

Trading mode is set by `TRADING_MODE` in `.env` (`paper` default / `confirm` /
`live`). Regardless of mode, the risk limits and the campaign drawdown halt apply.
