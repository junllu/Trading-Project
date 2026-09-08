"""Backtest engine — replay real history through the decision brain.

Reuses the same components as live trading (technical score, forecast, conviction
blend, volatility-scaled sizing, drawdown halt) so what you validate is what you
run. Produces standard metrics and writes the run to data/history.db.

By default, sources are technical + forecast only, so multi-year sweeps are
free and fast. Pass an `analyst` (e.g. LocalLLMAnalyst) and add "analyst" to a
setup's weights to fold in an LLM rating too — the analyst is called at most
once per rebalance (batched across all symbols), not once per bar.

Conviction strategies use NEXT-BAR FILLS: signal on close[t], fill on
close[t+1], so the decision never consumes the fill bar's return.
"""
from __future__ import annotations

import math
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from ..analysis import rsi
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
    realized_pnl: float = 0.0
    harvested_losses: float = 0.0          # sum of losses realized by conviction-driven sells ($, positive)
    harvest_events: int = 0                # count of sells that realized a loss
    costs_paid: float = 0.0                # $ given up to spread + slippage + commission
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
            "realized_pnl": round(self.realized_pnl, 2),
            "harvested_losses": round(self.harvested_losses, 2),
            "harvest_events": self.harvest_events,
        }


class Backtest:
    def __init__(self, setup: Setup, data: PriceData, starting_cash: float = 100_000.0,
                 store: Optional[SimStore] = None, warmup: int = 35, analyst=None,
                 costs=None):
        # Costs default to the realistic retail model, NOT to zero. A gross
        # backtest is not a result, and making friction opt-in guarantees it
        # gets left off exactly when the strategy trades most. Pass
        # CostModel.free() explicitly to reproduce an old gross number.
        from .costs import CostModel
        self.costs = costs if costs is not None else CostModel.retail_equity()
        self.setup = setup
        self.data = data
        self.starting_cash = starting_cash
        self.store = store
        self.warmup = warmup
        self.run_id = f"bt_{setup.name.split(':')[0]}_{uuid.uuid4().hex[:6]}"
        self.conviction = ConvictionEngine(setup.weights)
        self.forecaster = build_forecaster(setup.forecast_model)
        self.macro = MacroEngine()
        # Optional analyst (e.g. LocalLLMAnalyst) — only ever called when the
        # setup actually weights "analyst", so runs without one cost nothing.
        self.analyst = analyst if "analyst" in setup.weights else None

    def run(self) -> BacktestResult:
        d = self.data
        syms = d.symbols
        n = len(d)
        cash = self.starting_cash
        shares: dict[str, float] = {s: 0.0 for s in syms}
        cost_basis: dict[str, float] = {s: 0.0 for s in syms}   # total $ cost of currently held shares
        self._realized_pnl = 0.0
        self._costs_paid = 0.0
        self._harvested_losses = 0.0
        self._harvest_events = 0
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
                cost_basis[s] = shares[s] * px
                cash -= shares[s] * px
                trades += 1

        pending = None  # orders planned on close[t], filled on close[t+1]
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

            # NEXT-BAR FILLS: execute yesterday's plan at today's close first.
            # Signals are formed on close[t]; fills happen on close[t+1] so the
            # decision never uses the fill bar's return (same-bar look-ahead).
            if pending is not None:
                n_new, cash = self._fill_orders(
                    pending, t, prices, shares, cost_basis, cash)
                trades += n_new
                pending = None
                # refresh equity / halt after fills
                equity = cash + sum(shares[s] * prices[s] for s in syms)
                hwm = max(hwm, equity)
                dd = (hwm - equity) / hwm if hwm > 0 else 0.0
                max_dd = max(max_dd, dd)
                halted = self.setup.drawdown_halt > 0 and dd >= self.setup.drawdown_halt

            # Plan on rebalance days; fill tomorrow (skip last bar — nowhere to fill).
            if (self.setup.strategy == "conviction" and t >= self.warmup
                    and t % self.setup.rebalance_days == 0 and t + 1 < n):
                pending = self._plan_rebalance(
                    t, prices, shares, cost_basis, cash, equity, halted)

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
            realized_pnl=self._realized_pnl, harvested_losses=self._harvested_losses,
            harvest_events=self._harvest_events, costs_paid=self._costs_paid,
            equity_curve=[round(x, 2) for x in equity_curve],
        )
        if self.store:
            self.store.finish_run(self.run_id, result.to_dict())
            self.store.commit()
        return result

    # --- plan on close[t], fill on close[t+1] -------------------------------
    def _plan_rebalance(self, t: int, prices: dict, shares: dict, cost_basis: dict,
                        cash: float, equity: float, halted: bool) -> list[dict]:
        """Form orders from information available through bar t. No fills here."""
        s_setup = self.setup
        orders: list[dict] = []
        analyst_ratings: dict[str, float] = {}
        if self.analyst is not None:
            analyst_ratings = self._analyst_ratings(t, prices, shares, cost_basis)
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
            if "analyst" in s_setup.weights and s in analyst_ratings:
                inputs["analyst"] = analyst_ratings[s]
            if "macro" in s_setup.weights:
                mb = self.macro.symbol_bias(s, self.data.dates[t])
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
                    orders.append({
                        "symbol": s, "side": "buy", "value": buy_value,
                        "score": conv.score, "action": conv.action,
                        "signal_bar": t,
                    })
                    cash -= buy_value  # reserve so later names in the plan don't over-allocate
            elif conv.score < 0 and held_value > 0:
                sell_value = min(target_value, held_value)
                if sell_value > 1:
                    orders.append({
                        "symbol": s, "side": "sell", "value": sell_value,
                        "score": conv.score, "action": conv.action,
                        "signal_bar": t,
                    })
        return orders

    def _fill_orders(self, orders: list[dict], t: int, prices: dict, shares: dict,
                     cost_basis: dict, cash: float) -> tuple[int, float]:
        """Fill planned orders at bar t prices (the bar AFTER the signal)."""
        trades = 0
        for o in orders:
            s = o["symbol"]
            px = prices.get(s, 0.0)
            if px <= 0:
                continue
            if o["side"] == "buy":
                buy_value = o["value"]
                if buy_value > cash:
                    buy_value = cash
                if buy_value <= 1:
                    continue
                fill = self.costs.effective_buy_price(px)
                q = buy_value / fill
                self._costs_paid += buy_value - q * px
                shares[s] += q
                cost_basis[s] += buy_value
                cash -= buy_value
                trades += 1
                if self.store:
                    self.store.record_trade(
                        self.run_id, t, s, "buy", q, px, o["score"], o["action"])
            else:
                held_value = shares[s] * px
                sell_value = min(o["value"], held_value)
                if sell_value <= 1 or shares[s] <= 0:
                    continue
                q = sell_value / px
                fill = self.costs.effective_sell_price(px)
                proceeds = q * fill
                self._costs_paid += sell_value - proceeds
                avg_cost = cost_basis[s] / shares[s] if shares[s] > 1e-9 else px
                realized = (px - avg_cost) * q
                self._realized_pnl += realized
                if realized < 0:
                    self._harvested_losses += -realized
                    self._harvest_events += 1
                cost_basis[s] -= avg_cost * q
                shares[s] -= q
                cash += proceeds
                trades += 1
                if self.store:
                    self.store.record_trade(
                        self.run_id, t, s, "sell", q, px, o["score"], o["action"])
        return trades, cash

    def _rebalance(self, t: int, prices: dict, shares: dict, cost_basis: dict, cash: float,
                   equity: float, halted: bool) -> tuple[int, float]:
        """Backward-compatible: plan then fill same call (used only by legacy callers).
        Prefer the run() loop's next-bar path for honest results.
        """
        plan = self._plan_rebalance(t, prices, shares, cost_basis, cash, equity, halted)
        return self._fill_orders(plan, t, prices, shares, cost_basis, cash)

    def _analyst_ratings(self, t: int, prices: dict, shares: dict, cost_basis: dict) -> dict[str, float]:
        """One batched analyst call (all symbols) per rebalance, not per bar."""
        symbol_data = []
        for s in self.data.symbols:
            hist = self.data.closes[s][: t + 1]
            tech = technical_score(s, hist)
            r = rsi(hist, 14)
            px = prices[s]
            qty = shares[s]
            avg_cost = cost_basis[s] / qty if qty > 1e-9 else None
            symbol_data.append({
                "symbol": s,
                "price": round(px, 2),
                "technical": round(tech.score, 3) if tech.ready else 0.0,
                "rsi": round(r[-1], 1) if r and r[-1] is not None else None,
                "position_shares": round(qty, 4),
                "avg_cost": round(avg_cost, 4) if avg_cost is not None else None,
                "unrealized_pct": round((px / avg_cost - 1) * 100, 2) if avg_cost else None,
                "events": [],
            })
        try:
            result = self.analyst.analyze(symbol_data)
        except Exception:
            return {}
        return result.ratings

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
            store: Optional[SimStore] = None, analyst=None) -> list[dict]:
    """Run several setups over the same data; return results sorted by CAGR."""
    results = [Backtest(s, data, starting_cash, store, analyst=analyst).run().to_dict() for s in setups]
    return sorted(results, key=lambda r: -r["cagr_pct"])
