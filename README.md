# Automated Trading Portal

A personal portal for managing and automating stock trading and analysis across
**Robinhood** and **Webull**, with a unified web dashboard.

> ⚠️ **Read this first.** Neither Robinhood nor Webull offers an official public
> trading API. This project uses community-maintained libraries
> (`robin_stocks`, `webull`) that talk to the brokers' private endpoints.
> They can break without notice, and automated order placement may conflict
> with each broker's Terms of Service. **You** are responsible for how you use
> this. Trade with money you can afford to lose.

## What it does

- **Unified portfolio sync** — positions, cash, and P&L from every connected broker in one view.
- **Market data & analysis** — quotes, price history, and technical indicators (SMA, EMA, RSI, MACD).
- **Strategy engine** — pluggable strategies that turn market data into buy/sell signals.
- **Risk manager** — hard limits on position size, daily loss, and order count before anything reaches a broker.
- **Auto-execution** — signals flow to orders automatically, with a **kill-switch** and a **paper-trading default**.
- **Web dashboard** — a FastAPI app to watch it all live.

## Safety model

The executor has three modes, set by `TRADING_MODE`:

| Mode      | Behavior                                                            |
| --------- | ------------------------------------------------------------------ |
| `paper`   | (default) Orders fill against a simulated broker. No real money.    |
| `confirm` | Orders are prepared and queued, but wait for your explicit approval.|
| `live`    | Orders are sent to the real broker automatically.                  |

On top of the mode, a global **kill-switch** (`app/engine/executor.py` /
`POST /api/kill`) halts all execution instantly, and the **risk manager**
rejects any order that breaches your configured limits.

## Quick start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # fill in broker credentials (optional in paper mode)
cp config/config.example.yaml config/config.yaml

python run.py                 # starts the dashboard at http://localhost:8000
```

Run the tests (no broker libraries or network required):

```bash
pip install pytest
pytest
```

## Project layout

```
app/
  main.py            FastAPI app + dashboard
  config.py          settings & secrets loading
  security.py        local credential encryption
  brokers/           broker abstraction: base, paper, robinhood, webull
  data/              market data provider
  analysis/          technical indicators (pure Python)
  strategy/          strategy base + example strategies
  engine/            risk manager + auto-execution engine
  portfolio/         cross-broker portfolio aggregation
  api/               REST routes
  web/               dashboard templates & assets
tests/               unit tests for indicators, paper broker, risk
```

## Connecting real accounts

1. Set `TRADING_MODE=paper` and confirm everything behaves.
2. Add credentials to `.env` (they're encrypted at rest via `app/security.py`).
3. Enable the broker(s) in `config/config.yaml`.
4. Only when you're satisfied: set `TRADING_MODE=confirm`, then eventually `live`.

Webull additionally requires a device-id / trade-token handshake and often an
emailed or SMS security code on first login — the portal will prompt for it.
