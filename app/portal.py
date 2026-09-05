"""The central runtime that wires every component together.

One Portal instance owns the brokers, market data, strategies, risk manager,
executor, and portfolio service. `tick()` runs one full cycle:
refresh quotes -> evaluate strategies -> route signals through the executor.
The FastAPI app drives it on an interval; tests drive it directly.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from .brokers import build_brokers
from .brokers.base import BrokerBase
from .config import Settings, TradingMode, settings as global_settings
from .data import MarketData
from .engine import Executor, RiskManager
from .intel import IntelService
from .models import Signal
from .options import (
    cash_secured_put_candidates,
    covered_call_candidates,
    sell_the_news_plan,
)
from .portfolio import PortfolioService
from .portfolio.holdings import load_holdings, seed_paper_broker
from .strategy import build_strategy
from .strategy.base import Strategy, StrategyContext

log = logging.getLogger("portal")


@dataclass
class StrategyRun:
    strategy: Strategy
    symbols: list[str]


@dataclass
class Portal:
    settings: Settings = field(default_factory=lambda: global_settings)
    brokers: dict[str, BrokerBase] = field(default_factory=dict)
    market: MarketData = field(default_factory=MarketData)
    risk: Optional[RiskManager] = None
    executor: Optional[Executor] = None
    portfolio: Optional[PortfolioService] = None
    intel: IntelService = field(default_factory=IntelService)
    strategy_runs: list[StrategyRun] = field(default_factory=list)
    signal_log: list[Signal] = field(default_factory=list)
    held_symbols: list[str] = field(default_factory=list)

    _thread: Optional[threading.Thread] = None
    _stop: threading.Event = field(default_factory=threading.Event)

    # setup -----------------------------------------------------------------
    def build(self) -> "Portal":
        self.brokers = build_brokers(self.settings.brokers)
        paper = self.brokers.get("paper")
        live = {k: v for k, v in self.brokers.items() if k != "paper"}

        # Primary market-data source: first live broker, else paper.
        self.market.set_primary(live[next(iter(live))] if live else paper)

        self.risk = RiskManager(self.settings.risk, allowed_symbols=self.settings.watchlist)
        self.executor = Executor(
            risk=self.risk,
            mode=self.settings.mode,
            paper_broker=paper,
            live_brokers=live,
        )
        self.portfolio = PortfolioService(self.brokers)

        # Connect paper immediately; live brokers connect lazily on first order.
        if paper is not None and not paper.is_connected():
            paper.connect()

        # Seed the real holdings snapshot into the paper broker (until live sync).
        self.held_symbols = []
        if paper is not None:
            holdings = load_holdings()
            if holdings:
                self.held_symbols = seed_paper_broker(paper, holdings)

        self.strategy_runs = []
        for spec in self.settings.strategies:
            try:
                strat = build_strategy(spec["name"], spec.get("params", {}))
                self.strategy_runs.append(StrategyRun(strat, spec.get("symbols", [])))
            except KeyError as exc:
                log.warning("skipping strategy: %s", exc)
        return self

    def ensure_built(self) -> "Portal":
        """Build on first use if startup hasn't run yet (e.g. tests, cold API hit)."""
        if self.executor is None or self.risk is None:
            self.build()
        return self

    # one cycle -------------------------------------------------------------
    def tick(self) -> dict[str, Any]:
        self.ensure_built()
        symbols = self._all_symbols()
        quotes = self.market.refresh(symbols)
        prices = {s: q.price for s, q in quotes.items()}

        fired: list[Signal] = []
        assert self.executor is not None
        for run in self.strategy_runs:
            for symbol in run.symbols:
                ctx = StrategyContext(
                    symbol=symbol,
                    history=self.market.history(symbol),
                    position_qty=self._position_qty(symbol),
                    params=run.strategy.params,
                )
                for sig in run.strategy.evaluate(ctx):
                    fired.append(sig)
                    self.signal_log.append(sig)
                    self.executor.handle_signal(sig, prices.get(symbol, 0.0))

        self.signal_log = self.signal_log[-200:]
        return {"prices": prices, "signals_fired": len(fired)}

    # helpers ---------------------------------------------------------------
    def _all_symbols(self) -> list[str]:
        syms = set(self.settings.watchlist)
        syms.update(self.held_symbols)
        for run in self.strategy_runs:
            syms.update(run.symbols)
        return sorted(syms)

    # intelligence + options -----------------------------------------------
    def intel_briefing(self, force: bool = False, use_x: bool = False) -> dict[str, Any]:
        universe = self.held_symbols or self._all_symbols()
        return self.intel.briefing(universe, force=force, use_x=use_x).to_dict()

    def option_plans(self, days: int = 30) -> dict[str, Any]:
        """Covered calls on real lots, CSPs from cash, sell-the-news on events."""
        self.ensure_built()
        paper = self.brokers.get("paper")
        positions = paper.get_positions() if paper else []
        # Prefer the latest tick, then the broker's seeded last price, then cost.
        prices: dict[str, float] = {}
        for p in positions:
            last = self.market.last(p.symbol)
            if last is not None:
                prices[p.symbol] = last.price
            elif paper is not None:
                prices[p.symbol] = paper.get_quote(p.symbol).price
            else:
                prices[p.symbol] = p.avg_price

        covered = covered_call_candidates(positions, prices, days=days)
        cash = paper.get_account().cash if paper else 0.0
        csp = cash_secured_put_candidates(
            self.held_symbols or self.settings.watchlist, prices, cash, days=days,
        )

        # sell-the-news: cross real holdings with the latest intel sentiment
        brief = self.intel.briefing(self.held_symbols or self._all_symbols())
        stn = []
        held = {p.symbol: p.quantity for p in positions}
        for sym, score in brief.symbol_sentiment.items():
            plan = sell_the_news_plan(
                sym, prices.get(sym, 0.0), score, held.get(sym, 0.0),
                importance=0.7,
            )
            if plan:
                stn.append(plan.to_dict())

        return {
            "covered_calls": [p.to_dict() for p in covered],
            "cash_secured_puts": [p.to_dict() for p in csp],
            "sell_the_news": stn,
        }

    def _position_qty(self, symbol: str) -> float:
        if self.portfolio is None:
            return 0.0
        for name, broker in self.brokers.items():
            try:
                for p in broker.get_positions():
                    if p.symbol == symbol:
                        return p.quantity
            except Exception:
                continue
        return 0.0

    # background loop -------------------------------------------------------
    def start_loop(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="portal-loop", daemon=True)
        self._thread.start()
        log.info("portal loop started (interval=%ss, mode=%s)", self.settings.loop_interval, self.settings.mode.value)

    def _run_loop(self) -> None:
        interval = max(5, self.settings.loop_interval)
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception:  # keep the loop alive no matter what
                log.exception("tick failed")
            self._stop.wait(interval)

    def stop_loop(self) -> None:
        self._stop.set()

    # convenience for the dashboard ----------------------------------------
    def status(self) -> dict[str, Any]:
        self.ensure_built()
        assert self.executor is not None and self.risk is not None
        paper = self.brokers.get("paper")
        prices: dict[str, float] = {}
        for s in self._all_symbols():
            last = self.market.last(s)
            if last is not None:
                prices[s] = last.price
            elif paper is not None:
                prices[s] = paper.get_quote(s).price   # seeded last price, not 0
            else:
                prices[s] = 0.0
        snap = self.portfolio.snapshot(prices) if self.portfolio else {}
        return {
            "mode": self.settings.mode.value,
            "killed": self.executor.killed,
            "loop_running": bool(self._thread and self._thread.is_alive()),
            "risk": self.risk.status(),
            "portfolio": snap,
            "pending_orders": [self._order_dict(o) for o in self.executor.pending],
            "recent_orders": [self._order_dict(o) for o in self.executor.history[-25:][::-1]],
            "recent_signals": [self._signal_dict(s) for s in self.signal_log[-25:][::-1]],
            "watchlist": [{"symbol": s, "price": prices.get(s, 0.0)} for s in self._all_symbols()],
            "intel_sources": self.intel.sources_live,
            "held_symbols": self.held_symbols,
        }

    @staticmethod
    def _order_dict(o) -> dict:
        return {
            "id": o.id, "symbol": o.symbol, "side": o.side.value,
            "quantity": round(o.quantity, 4), "status": o.status.value,
            "filled_price": o.filled_price, "reason": o.reason, "broker": o.broker,
        }

    @staticmethod
    def _signal_dict(s: Signal) -> dict:
        return {"symbol": s.symbol, "side": s.side.value, "strategy": s.strategy, "note": s.note}


# process-wide singleton, built on import by the web app
portal = Portal()
