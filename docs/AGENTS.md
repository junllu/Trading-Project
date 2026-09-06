# Agents, automation & local models

## You already have a multi-agent system

The conviction engine is an **ensemble of specialist agents** — each source is an
agent that scores a symbol, and the blend is their weighted vote (with a visible
breakdown). Today's agents:

| Agent (source) | What it reasons about | Backed by |
|---|---|---|
| technical  | trend / momentum (SMA/EMA/RSI/MACD) | pure Python |
| forecast   | forward-return prediction | naive, or a Hugging Face model (docs/ML.md) |
| macro      | policy-cycle / election-regime sector tilt | the 20-year timeline |
| sentiment  | event / news tone | lexicon (or FinBERT) |
| analyst    | holistic per-symbol view | **heuristic, Claude API, or a LOCAL model** |

The self-correction loop re-weights the agents by how well each *actually*
predicts — so a weak agent quietly loses influence. Add an agent by adding a
source to the blend.

## Running agents on LOCAL models (Ollama)

Run the analyst agent on a model on your own machine — free, private, offline:

```bash
# 1. install Ollama (ollama.com), then pull a model:
ollama pull llama3.1            # or qwen2.5, mistral, deepseek-r1, ...
ollama serve                     # serves http://localhost:11434
```
```yaml
# 2. config/config.yaml
agent:
  analyst_provider: ollama
  analyst_model: llama3.1
```

If the server is down or the model is missing, the analyst degrades to the
heuristic automatically — nothing breaks.

## Multiple agents (an ensemble of local models)

Because the analyst is just a provider, you can run **several** and blend them —
e.g. a fast model for breadth and a reasoning model for depth:

- `llama3.1` as the day-to-day analyst,
- `deepseek-r1` (reasoning) as a second "macro strategist" agent,
- Claude API reserved for the weekly deep review.

Each is a separate `build_analyst(provider, model)` instance; average their
ratings, or give each its own weighted source in the conviction blend. This is
how you scale to a team of agents without a cloud bill.

## Automation & monitoring

**Automate the daily routine:**
- In-app: the `DailyScheduler` fires `agent.run_at` (default 09:00) — start it
  from the dashboard or `POST /api/agent/schedule/start`.
- OS-level (preferred for reliability): cron / Task Scheduler calling
  `python -m app.agent` before the open — it writes `data/trade_plan.json`.

**Execute (approval-gated):** the local Claude with the robinhood-trading MCP
runs `/trade`, which reads the plan, checks guardrails, asks you, and places
orders.

**Monitor every action:**
- The **dashboard** — live campaign progress, drawdown vs halt, recent orders,
  conviction table, macro regime.
- **`data/history.db`** (SQLite) — a durable audit trail of every trade, event,
  equity point, and self-corrected weight, per run.
- The **daily report** / trade plan — the morning brief with the full rationale.

Guardrails run on every cycle regardless of who triggered it: the drawdown halt,
the kill-switch, per-order/position limits, and (for real orders) your approval.
