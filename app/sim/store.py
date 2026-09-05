"""Durable history for simulations and (later) live trading.

A single SQLite file (data/history.db, stdlib only) records everything the loop
does so it survives restarts and can be reviewed or learned from:

  runs     — one row per simulation/session
  events   — market/policy events and decisions, timestamped
  trades   — every fill (side, qty, price, conviction, reason)
  equity   — the equity curve + drawdown per step
  weights  — the self-correcting conviction weights over time

Everything is keyed by run_id so multiple experiments coexist.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from ..config import ROOT

DB_PATH = ROOT / "data" / "history.db"


class SimStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = path or DB_PATH
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY, created REAL, kind TEXT,
                config TEXT, summary TEXT
            );
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts REAL,
                sim_day INTEGER, type TEXT, symbol TEXT, detail TEXT
            );
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts REAL,
                sim_day INTEGER, symbol TEXT, side TEXT, qty REAL, price REAL,
                value REAL, conviction REAL, reason TEXT
            );
            CREATE TABLE IF NOT EXISTS equity (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts REAL,
                sim_day INTEGER, equity REAL, cash REAL, positions_value REAL,
                drawdown REAL, phase TEXT
            );
            CREATE TABLE IF NOT EXISTS weights (
                id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT, ts REAL,
                sim_day INTEGER, weights TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, sim_day);
            CREATE INDEX IF NOT EXISTS idx_trades_run ON trades(run_id, sim_day);
            CREATE INDEX IF NOT EXISTS idx_equity_run ON equity(run_id, sim_day);
            """
        )
        self.conn.commit()

    # writes -----------------------------------------------------------------
    def start_run(self, run_id: str, kind: str, config: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO runs(run_id, created, kind, config, summary) VALUES (?,?,?,?,?)",
            (run_id, time.time(), kind, json.dumps(config, default=str), ""),
        )
        self.conn.commit()

    def finish_run(self, run_id: str, summary: dict[str, Any]) -> None:
        self.conn.execute("UPDATE runs SET summary=? WHERE run_id=?",
                          (json.dumps(summary, default=str), run_id))
        self.conn.commit()

    def record_event(self, run_id: str, sim_day: int, etype: str, symbol: str, detail: dict) -> None:
        self.conn.execute(
            "INSERT INTO events(run_id, ts, sim_day, type, symbol, detail) VALUES (?,?,?,?,?,?)",
            (run_id, time.time(), sim_day, etype, symbol, json.dumps(detail, default=str)),
        )

    def record_trade(self, run_id: str, sim_day: int, symbol: str, side: str, qty: float,
                     price: float, conviction: float, reason: str) -> None:
        self.conn.execute(
            "INSERT INTO trades(run_id, ts, sim_day, symbol, side, qty, price, value, conviction, reason)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (run_id, time.time(), sim_day, symbol, side, qty, price, qty * price, conviction, reason),
        )

    def record_equity(self, run_id: str, sim_day: int, equity: float, cash: float,
                      positions_value: float, drawdown: float, phase: str) -> None:
        self.conn.execute(
            "INSERT INTO equity(run_id, ts, sim_day, equity, cash, positions_value, drawdown, phase)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (run_id, time.time(), sim_day, equity, cash, positions_value, drawdown, phase),
        )

    def record_weights(self, run_id: str, sim_day: int, weights: dict[str, float]) -> None:
        self.conn.execute(
            "INSERT INTO weights(run_id, ts, sim_day, weights) VALUES (?,?,?,?)",
            (run_id, time.time(), sim_day, json.dumps(weights)),
        )

    def commit(self) -> None:
        self.conn.commit()

    # reads ------------------------------------------------------------------
    def equity_curve(self, run_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT sim_day, equity, drawdown, phase FROM equity WHERE run_id=? ORDER BY sim_day", (run_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def trades(self, run_id: str, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT sim_day, symbol, side, qty, price, value, conviction, reason FROM trades"
            " WHERE run_id=? ORDER BY id DESC LIMIT ?", (run_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    def events(self, run_id: str, limit: int = 100) -> list[dict]:
        rows = self.conn.execute(
            "SELECT sim_day, type, symbol, detail FROM events WHERE run_id=? ORDER BY id DESC LIMIT ?",
            (run_id, limit)
        ).fetchall()
        return [dict(r) for r in rows]

    def weight_history(self, run_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT sim_day, weights FROM weights WHERE run_id=? ORDER BY sim_day", (run_id,)
        ).fetchall()
        return [{"sim_day": r["sim_day"], **json.loads(r["weights"])} for r in rows]

    def list_runs(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT run_id, created, kind, summary FROM runs ORDER BY created DESC LIMIT ?", (limit,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["summary"] = json.loads(d["summary"]) if d["summary"] else {}
            except Exception:
                d["summary"] = {}
            out.append(d)
        return out

    def close(self) -> None:
        self.conn.close()
