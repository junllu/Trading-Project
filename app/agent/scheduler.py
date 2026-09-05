"""A lightweight daily scheduler.

Runs a callable once per day at a configured local time (HH:MM), in a daemon
thread. Deliberately dependency-free — no APScheduler — so it just works. For
production you may prefer cron or a systemd timer calling `python -m app.agent`;
this keeps everything self-contained for the portal process.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Callable

log = logging.getLogger("portal.scheduler")


class DailyScheduler:
    def __init__(self, job: Callable[[], object], at: str = "09:00"):
        self.job = job
        self.at = at
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.last_run: float | None = None
        self.next_run: float | None = None

    def _seconds_until_next(self) -> float:
        now = datetime.now()
        try:
            hh, mm = (int(x) for x in self.at.split(":"))
        except ValueError:
            hh, mm = 9, 0
        target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        self.next_run = target.timestamp()
        return (target - now).total_seconds()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="daily-agent", daemon=True)
        self._thread.start()
        log.info("daily scheduler started (fires at %s local)", self.at)

    def _loop(self) -> None:
        while not self._stop.is_set():
            wait = self._seconds_until_next()
            # Wake at least hourly so a changed clock / DST doesn't strand us.
            if self._stop.wait(min(wait, 3600)):
                return
            if wait <= 3600 and not self._stop.is_set():
                self._fire()

    def _fire(self) -> None:
        try:
            log.info("daily agent firing")
            self.job()
            self.last_run = time.time()
        except Exception:
            log.exception("daily agent run failed")

    def run_now(self) -> object:
        """Fire immediately (manual trigger), returning the job result."""
        result = self.job()
        self.last_run = time.time()
        return result

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        return {
            "at": self.at,
            "running": bool(self._thread and self._thread.is_alive()),
            "last_run": self.last_run,
            "next_run": self.next_run,
        }
