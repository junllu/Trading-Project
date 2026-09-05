"""Entry point: launch the trading portal dashboard.

    python run.py

Reads host/port from the environment (see .env.example) and starts uvicorn.
"""
from __future__ import annotations

import uvicorn

from app.config import settings


def main() -> None:
    print(f"Trading Portal starting in '{settings.mode.value}' mode")
    print(f"Dashboard:  http://{settings.host}:{settings.port}")
    if settings.mode.value == "live":
        print("⚠️  LIVE MODE: orders will be sent to real brokers. Kill-switch is on the dashboard.")
    uvicorn.run("app.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    main()
