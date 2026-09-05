# Setup & Desktop Launcher

## Fastest path (macOS)

1. Download/clone this project to a folder you'll keep (e.g. `~/Trading-Project`).
2. In Finder, **double-click `install_desktop_shortcut.command`** — this puts a
   **Trading Portal** launcher on your Desktop.
   - If macOS warns "unidentified developer": right-click → **Open** → **Open**.
3. From now on, **double-click "Trading Portal" on your Desktop** to start it.
   The first launch creates a virtual environment and installs dependencies
   (a minute or two); later launches are instant. Your browser opens to the
   dashboard automatically at <http://127.0.0.1:8000>.
4. To stop it, close the Terminal window it opened (or press `Ctrl+C` there).

You can also just double-click **`launch.command`** inside the project folder
directly, without installing the Desktop shortcut.

## Windows

Double-click **`launch.bat`**. Same behavior: first run sets things up, then the
dashboard opens at <http://127.0.0.1:8000>. Requires Python 3 installed and on PATH.

## Linux / manual

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python run.py           # dashboard at http://localhost:8000
```

## Adding your keys (optional but recommended)

Edit `.env` (created automatically on first launch):

- `ANTHROPIC_API_KEY` — turns the analyst from the built-in heuristic into
  **Claude (Opus)** reasoning over your book. Get one at
  <https://console.anthropic.com/>.
- `XAI_API_KEY` — optional; only if you want Grok live-search events too.

Restart the portal after editing `.env`.

## The Daily Agent

- On the dashboard, **▶ Run Daily Analysis** runs the full routine now:
  refresh data → score technicals → events + geopolitics → analyst ratings →
  blended conviction → sized orders through the risk gate.
- **Start Schedule** runs it automatically every day at the time in
  `config/config.yaml` (`agent.run_at`, default 09:00 local).
- Prefer the OS scheduler? Point cron/Task Scheduler at:
  ```bash
  cd /path/to/Trading-Project && .venv/bin/python -m app.agent
  ```

## Safety reminder

Defaults to **paper mode** (`TRADING_MODE=paper` in `.env`). The agent will
analyze and simulate. Switch to `confirm` (orders wait for your tap) or `live`
(real orders) only when you're satisfied — the kill-switch and risk limits apply
in every mode.
