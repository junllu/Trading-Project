"""Entry point: launch the trading portal dashboard.

    python run.py

Finds a free port (so it never collides with another app already on 8000),
opens your browser to the correct URL automatically, and starts the server.
"""
from __future__ import annotations

import socket
import threading
import webbrowser

import uvicorn

from app.config import settings


def _port_is_free(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
            return True
        except OSError:
            return False


def _find_free_port(host: str, preferred: int) -> int:
    # Try the preferred port, then scan upward for the first free one.
    for port in [preferred, *range(preferred + 1, preferred + 50)]:
        if _port_is_free(host, port):
            return port
    return preferred  # give up gracefully; uvicorn will report the bind error


def main() -> None:
    host = settings.host or "127.0.0.1"
    preferred = settings.port or 8000
    port = _find_free_port(host, preferred)
    url = f"http://{host}:{port}"

    print("=" * 46)
    print(f"  Trading Portal — {settings.mode.value.upper()} mode")
    if port != preferred:
        print(f"  (port {preferred} was busy — using {port} instead)")
    print(f"  Dashboard: {url}")
    print("  Leave this window open. Press Ctrl+C to stop.")
    if settings.mode.value == "live":
        print("  LIVE MODE: real orders will be sent. Kill-switch is on the dashboard.")
    print("=" * 46)

    # Open the browser once the server has had a moment to come up.
    threading.Timer(2.0, lambda: webbrowser.open(url)).start()

    uvicorn.run("app.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
