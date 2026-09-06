"""Backtest engine — replay real history through the decision brain.

Reuses the same components as live trading (technical score, forecast, conviction
blend, volatility-scaled sizing, drawdown halt) so what you validate is what you
run. Produces standard metrics and writes the run to data/history.db.

No LLM calls: backtest sources are technical + forecast only, so multi-year
sweeps are free and fast.
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from ..analytics import ConvictionEngine
from ..analytics.technical import technical_score
from ..engine.sizing import realized_vol
from ..macro import MacroEngine
from ..ml import build_forecaster
from ..sim.store import SimStore
from .data import PriceData
from .setups import Setup


@dataclass
class BacktestResult:
    setup: str
    symbols: list[str]
    days: int
    start_equity: float
    final_equity: float
    total_return: float
    cagr: float
    max_drawdown: float
    sharpe: float
    volatility: float
    win_rate: float
    trades: int
    halted_days: int
    run_id: str
    equity_curve: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "setup": self.setup, "symbols": self.symbols, "days": self.days,
            "start_equity": round(self.start_equity, 2), "final_equity": round(self.final_equity, 2),
            "total_return_pct": round(self.total_return * 100, 2),
            "cagr_pct": round(self.cagr * 100, 2),
            "max_drawdown_pct": round(self.max_drawdown * 100, 2),
            "sharpe": round(self.sharpe, 2), "volatility_pct": round(self.volatility * 100, 2),
            "win_rate_pct": round(self.win_rate * 100, 1), "trades": self.trades,
            "halted_days": self.halted_days, "run_id": self.run_id,
        }


class Backtest:
    def __init__(self, setup: Setup, data: PriceData, starting_cash: float = 100_000.0,
                 store: Optional[SimStore] = None, warmup: int = 35):
        self.setup = setup
        self.data = data
        self.starting_cash = starting_cash
        self.store = store
        self.warmup = warmup
        self.run_id = f"bt_{setup.name.split(':')[0]}_{uuid.uuid4().hex[:6]}"
        self.conviction = ConvictionEngine(setup.weights)
        self.forecaster = build_forecaster(setup.forecast_model)
        self.macro = MacroEngine()

    def run(self) -> BacktestResult:
        d = self.data
        syms = d.symbols
        n = len(d)
        cash = self.starting_cash
        shares: dict[str, float] = {s: 0.0 for s in syms}
        hwm = self.starting_cash
        equity_curve: list[float] = []
        rets: list[float] = []
        trades = 0
        halted_days = 0
        max_dd = 0.0
        prev_equity = self.starting_cash

        if self.store:
            self.store.start_run(self.run_id, "backtest", self.setup.to_dict())

        # Buy & hold: deploy equally on the first tradable day.
        if self.setup.strategy == "buy_hold":
            first = self.warmup
            per = cash / len(syms)
            for s in syms:
                px = d.closes[s][first]
                shares[s] = per / px if px else 0.0
                cash -= shares[s] * px
                trades += 1

        for t in range(n):
            prices = {s: d.closes[s][t] for s in syms}
            equity = cash + sum(shares[s] * prices[s] for s in syms)
            hwm = max(hwm, equity)
            dd = (hwm - equity) / hwm if hwm > 0 else 0.0
            max_dd = max(max_dd, dd)
            halted = self.setup.drawdown_halt > 0 and dd >= self.setup.drawdown_halt
            if halted:
                halted_days += 1

            equity_curve.append(equity)
            if t > 0 and prev_equity > 0:
                rets.append(equity / prev_equity - 1.0)
            prev_equity = equity

            # trade only on rebalance days, after warmup, for conviction strategies
            if (self.setup.strategy == "conviction" and t >= self.warmup
                    and t % self.setup.rebalance_days == 0):
                n_new, cash = self._rebalance(t, prices, shares, cash, equity, halted)
                trades += n_new

            if self.store and t % 5 == 0:
                self.store.record_equity(self.run_id, t, equity, cash,
                                         equity - cash, dd, "backtest")

        final_equity = equity_curve[-1] if equity_curve else self.starting_cash
        result = BacktestResult(
            setup=self.setup.name, symbols=syms, days=n,
            start_equity=self.starting_cash, final_equity=final_equity,
            total_return=(final_equity / self.starting_cash - 1) if self.starting_cash else 0.0,
            cagr=self._cagr(final_equity, n), max_drawdown=max_dd,
            sharpe=self._sharpe(rets), volatility=self._vol(rets),
            win_rate=(sum(1 for r in rets if r > 0) / len(rets)) if rets else 0.0,
            trades=trades, halted_days=halted_days, run_id=self.run_id,
            equity_curve=[round(x, 2) for x in equity_curve],
        )
        if self.store:
            self.store.finish_run(self.run_id, result.to_dict())
            self.store.commit()
        return result

    # --- one rebalance -----------------------------------------------------
    def _rebalance(self, t: int, prices: dict, shares: dict, cash: float,
                   equity: float, halted: bool) -> tuple[int, float]:
        s_setup = self.setup
        trades = 0
        # score every symbol
        ranked = []
        for s in self.data.symbols:
            hist = self.data.closes[s][: t + 1]
            tech = technical_score(s, hist)
            fc = self.forecaster.predict(s, hist)
            inputs = {}
            if "technical" in s_setup.weights and tech.ready:
                inputs["technical"] = tech.score
            if "forecast" in s_setup.weights and fc.confidence > 0:
                inputs["forecast"] = fc.score()
            if "macro" in s_setup.weights:
                mb = self.macro.symbol_bias(s, self.data.dates[t])   # replay-date policy tilt
                if abs(mb) > 0.02:
                    inputs["macro"] = mb
            conv = self.conviction.blend(s, inputs)
            ranked.append((s, conv, hist))
        ranked.sort(key=lambda x: -x[1].score)

        for s, conv, hist in ranked:
            px = prices[s]
            if px <= 0:
                continue
            strength = abs(conv.score)
            if strength < s_setup.entry_threshold:
                continue
            vol = realized_vol(hist)
            vscalar = 1.0 if vol <= 0 else max(0.4, min(1.5, s_setup.target_vol / vol))
            target_value = equity * s_setup.base_frac * strength * vscalar
            held_value = shares[s] * px
            if conv.score > 0 and not halted:
                room = equity * s_setup.max_position_frac - held_value
                buy_value = max(0.0, min(target_value, room, cash))
                if buy_value > 1:
                    q = buy_value / px
                    shares[s] += q
                    cash -= buy_value
                    trades += 1
                    if self.store:
                        self.store.record_trade(self.run_id, t, s, "buy", q, px, conv.score, conv.action)
            elif conv.score < 0 and held_value > 0:
                sell_value = min(target_value, held_value)
                q = sell_value / px
                shares[s] -= q
                cash += sell_value
                trades += 1
                if self.store:
                    self.store.record_trade(self.run_id, t, s, "sell", q, px, conv.score, conv.action)
        return trades, cash

    # --- metrics -----------------------------------------------------------
    def _cagr(self, final: float, n: int) -> float:
        if self.starting_cash <= 0 or n < 2 or final <= 0:
            return 0.0
        years = n / 252.0
        return (final / self.starting_cash) ** (1 / years) - 1.0 if years > 0 else 0.0

    @staticmethod
    def _sharpe(rets: list[float]) -> float:
        if len(rets) < 2:
            return 0.0
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        std = math.sqrt(var)
        return (mean / std) * math.sqrt(252) if std > 0 else 0.0

    @staticmethod
    def _vol(rets: list[float]) -> float:
        if len(rets) < 2:
            return 0.0
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        return math.sqrt(var) * math.sqrt(252)


def compare(setups: list[Setup], data: PriceData, starting_cash: float = 100_000.0,
            store: Optional[SimStore] = None) -> list[dict]:
    """Run several setups over the same data; return results sorted by CAGR."""
    results = [Backtest(s, data, starting_cash, store).run().to_dict() for s in setups]
    return sorted(results, key=lambda r: -r["cagr_pct"])
