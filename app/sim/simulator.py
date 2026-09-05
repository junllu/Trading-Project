"""Self-correcting trading simulator.

Drives its own price world through a three-phase regime — up-cycle → ~2028
saturation (plateau/drawdown) → recovery — with injectable policy/geopolitical
event shocks (e.g. tariff / export-control actions). Each simulated day it:

  1. advances prices (regime drift + volatility, plus event shocks),
  2. scores every symbol from four sources (technical, analyst, event-sentiment,
     geopolitical) and blends them into conviction with the CURRENT weights,
  3. sizes and executes on the paper broker under the campaign guardrails
     (focus-only buys, de-risk into the saturation, drawdown halt),
  4. records every trade, event, and equity point to the SQLite store, and
  5. periodically SELF-CORRECTS: it measures which sources actually predicted
     the next few days' returns and shifts weight toward the ones that were
     right — then keeps going.

It is deterministic given a seed, so experiments are repeatable. This is how you
answer "1-2 names vs the whole book" with evidence instead of opinion.
"""
from __future__ import annotations

import math
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from ..analytics import ConvictionEngine
from ..analytics.technical import technical_score
from ..brokers.paper import PaperBroker
from ..engine.sizing import SizingParams, size_order_value
from ..models import Order, OrderType, Position, Side
from ..options.pricing import implied_vol_guess
from .store import SimStore


@dataclass
class SimConfig:
    symbols: list[str]
    focus_symbols: list[str]
    days: int = 300
    starting_cash: float = 50_000.0
    starting_positions: dict[str, tuple[float, float]] = field(default_factory=dict)  # sym -> (shares, avg)
    seed: int = 7
    # regime drifts (annualized) for focus vs non-focus, per phase
    up_drift: float = 0.55
    saturation_drift: float = -0.25
    recovery_drift: float = 0.35
    noncore_drift: float = 0.06
    # phase boundaries as fractions of `days`
    accumulate_frac: float = 0.55
    saturation_frac: float = 0.78
    # event engine
    event_prob: float = 0.06                 # per-day chance of a market-moving event
    event_shock: float = 0.05                # ~5% price shock magnitude
    # strategy
    weights: Optional[dict[str, float]] = None
    entry_threshold: float = 0.2
    base_budget: float = 1500.0
    max_order_value: float = 3000.0
    max_position_value: float = 20_000.0
    drawdown_halt: float = 0.20
    # self-correction
    self_correct: bool = True
    correct_every: int = 20
    lookback: int = 60
    forward: int = 5


@dataclass
class SimResult:
    run_id: str
    start_equity: float
    final_equity: float
    total_return: float
    max_drawdown: float
    trades: int
    halted_days: int
    final_weights: dict[str, float]
    source_hit_rates: dict[str, float]
    phases: dict[str, int]

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "start_equity": round(self.start_equity, 2),
            "final_equity": round(self.final_equity, 2),
            "total_return_pct": round(self.total_return * 100, 2),
            "max_drawdown_pct": round(self.max_drawdown * 100, 2),
            "trades": self.trades,
            "halted_days": self.halted_days,
            "final_weights": {k: round(v, 3) for k, v in self.final_weights.items()},
            "source_hit_rates": {k: round(v, 3) for k, v in self.source_hit_rates.items()},
            "phases": self.phases,
        }


class Simulator:
    SOURCES = ["technical", "analyst", "sentiment", "geopolitical"]

    def __init__(self, cfg: SimConfig, store: Optional[SimStore] = None):
        self.cfg = cfg
        self.store = store or SimStore()
        self.rng = random.Random(cfg.seed)
        self.run_id = f"sim_{int(time.time())}_{uuid.uuid4().hex[:6]}"
        weights = cfg.weights or {"technical": 0.4, "analyst": 0.3, "sentiment": 0.15, "geopolitical": 0.15}
        self.conviction = ConvictionEngine({k: weights.get(k, 0.0) for k in self.SOURCES})
        self.broker = PaperBroker(starting_cash=cfg.starting_cash)
        self.broker.connect()
        self.vol = {s: implied_vol_guess(s) for s in cfg.symbols}
        self.prices: dict[str, float] = {s: 100.0 for s in cfg.symbols}
        self.closes: dict[str, list[float]] = {s: [] for s in cfg.symbols}
        self.sentiment: dict[str, float] = {s: 0.0 for s in cfg.symbols}
        self.geo_bias: dict[str, float] = {s: 0.0 for s in cfg.symbols}
        self.score_log: list[dict] = []       # {day, symbol, scores}
        self.hwm = 0.0

    # --- regime ------------------------------------------------------------
    def _phase(self, day: int) -> str:
        f = day / max(1, self.cfg.days)
        if f < self.cfg.accumulate_frac:
            return "accumulate"
        if f < self.cfg.saturation_frac:
            return "saturation"
        return "recovery"

    def _drift(self, symbol: str, phase: str) -> float:
        focus = symbol in self.cfg.focus_symbols
        if not focus:
            return self.cfg.noncore_drift
        return {"accumulate": self.cfg.up_drift, "saturation": self.cfg.saturation_drift,
                "recovery": self.cfg.recovery_drift}[phase]

    def _derisk(self, day: int, phase: str) -> float:
        # Scale risk down approaching and during the saturation, cautious in recovery.
        return {"accumulate": 1.0, "saturation": 0.25, "recovery": 0.6}[phase]

    # --- price + event engine ---------------------------------------------
    def _step_prices(self, day: int, phase: str) -> None:
        dt = 1.0 / 252.0
        for s in self.cfg.symbols:
            mu = self._drift(s, phase)
            vol = self.vol[s]
            z = self.rng.gauss(0, 1)
            self.prices[s] = max(0.5, self.prices[s] * math.exp((mu - 0.5 * vol * vol) * dt + vol * math.sqrt(dt) * z))
            # sentiment/geo decay toward zero each day
            self.sentiment[s] *= 0.8
            self.geo_bias[s] *= 0.85

    def _maybe_event(self, day: int) -> None:
        if self.rng.random() > self.cfg.event_prob:
            return
        etype = self.rng.choice(["policy", "geopolitical", "earnings"])
        # Target a focus name when it's in this universe, else any tracked symbol.
        candidates = [s for s in self.cfg.focus_symbols if s in self.prices] or self.cfg.symbols
        target = self.rng.choice(candidates)
        sign = self.rng.choice([-1, 1, 1])           # policy skews slightly bullish for the cycle
        if etype == "policy":
            sign = self.rng.choice([-1, -1, 1])      # tariffs/export-controls skew bearish for semis
        mag = self.cfg.event_shock * self.rng.uniform(0.5, 1.5)
        # apply an immediate price shock and set decaying sentiment/geo
        self.prices[target] = max(0.5, self.prices[target] * (1 + sign * mag))
        self.sentiment[target] = max(-1, min(1, self.sentiment[target] + sign * mag * 6))
        if etype in ("policy", "geopolitical"):
            self.geo_bias[target] = max(-1, min(1, self.geo_bias[target] + sign * mag * 5))
        headline = {
            "policy": f"Policy action hits {target} ({'tailwind' if sign > 0 else 'headwind'})",
            "geopolitical": f"Geopolitical development affecting {target}",
            "earnings": f"{target} {'beats' if sign > 0 else 'misses'} expectations",
        }[etype]
        self.store.record_event(self.run_id, day, etype, target,
                                {"headline": headline, "sign": sign, "shock_pct": round(sign * mag * 100, 2)})

    # --- scoring -----------------------------------------------------------
    def _analyst_score(self, symbol: str, tech: float) -> float:
        # Heuristic stand-in for the Claude analyst: technical with mild noise.
        return max(-1.0, min(1.0, tech + self.rng.gauss(0, 0.1)))

    def _scores(self, symbol: str) -> dict[str, Optional[float]]:
        ts = technical_score(symbol, self.closes[symbol])
        tech = ts.score if ts.ready else None
        return {
            "technical": tech,
            "analyst": self._analyst_score(symbol, tech) if tech is not None else None,
            "sentiment": self.sentiment[symbol] if abs(self.sentiment[symbol]) > 0.02 else None,
            "geopolitical": self.geo_bias[symbol] if abs(self.geo_bias[symbol]) > 0.02 else None,
        }

    # --- self-correction ---------------------------------------------------
    def _self_correct(self, day: int) -> None:
        k = self.cfg.forward
        cutoff_lo = day - self.cfg.lookback
        new_hits: dict[str, list[int]] = {s: [0, 0] for s in self.SOURCES}  # [hits, total]
        for entry in self.score_log:
            d = entry["day"]
            if d < cutoff_lo or d + k >= day:
                continue
            sym = entry["symbol"]
            closes = self.closes[sym]
            if d + k - 1 >= len(closes) or d - 1 < 0:
                continue
            r = closes[d + k - 1] / closes[d - 1] - 1.0
            if abs(r) < 1e-4:
                continue
            for src, val in entry["scores"].items():
                if val is None or abs(val) < 0.05:
                    continue
                new_hits[src][1] += 1
                if (val > 0) == (r > 0):
                    new_hits[src][0] += 1
        # nudge weights toward better predictors
        w = dict(self.conviction.weights)
        prev_total = sum(w.get(s, 0.0) for s in self.SOURCES) or 1.0
        for s in self.SOURCES:
            hits, total = new_hits[s]
            if total >= 5:
                hit_rate = hits / total
                w[s] = max(0.02, w.get(s, 0.0) * (0.5 + hit_rate))   # 0.5x..1.5x
        # renormalize to keep the overall scale stable
        scale = prev_total / (sum(w[s] for s in self.SOURCES) or 1.0)
        for s in self.SOURCES:
            w[s] *= scale
        self.conviction = ConvictionEngine(w)
        self.store.record_weights(self.run_id, day, {s: round(w[s], 4) for s in self.SOURCES})

    def hit_rates(self) -> dict[str, float]:
        k = self.cfg.forward
        acc: dict[str, list[int]] = {s: [0, 0] for s in self.SOURCES}
        for entry in self.score_log:
            d = entry["day"]
            closes = self.closes[entry["symbol"]]
            if d + k - 1 >= len(closes) or d - 1 < 0:
                continue
            r = closes[d + k - 1] / closes[d - 1] - 1.0
            if abs(r) < 1e-4:
                continue
            for src, val in entry["scores"].items():
                if val is None or abs(val) < 0.05:
                    continue
                acc[src][1] += 1
                if (val > 0) == (r > 0):
                    acc[src][0] += 1
        return {s: (acc[s][0] / acc[s][1] if acc[s][1] else 0.0) for s in self.SOURCES}

    # --- main loop ---------------------------------------------------------
    def run(self) -> SimResult:
        cfg = self.cfg
        # seed starting positions (from the real book, if provided)
        for sym, (shares, avg) in cfg.starting_positions.items():
            if sym in self.prices:
                self.prices[sym] = avg
                self.broker._positions[sym] = Position(sym, shares, avg, "sim")
        self.store.start_run(self.run_id, "simulation", cfg.__dict__)

        start_equity = self._equity()
        self.hwm = start_equity
        max_dd = 0.0
        halted_days = 0
        phase_counts: dict[str, int] = {}

        sizing = SizingParams(base_budget=cfg.base_budget, entry_threshold=cfg.entry_threshold,
                              max_budget=cfg.max_order_value)

        for day in range(1, cfg.days + 1):
            phase = self._phase(day)
            phase_counts[phase] = phase_counts.get(phase, 0) + 1
            self._step_prices(day, phase)
            self._maybe_event(day)
            for s in cfg.symbols:
                self.broker.set_price(s, self.prices[s])
                self.closes[s].append(self.prices[s])

            equity = self._equity()
            self.hwm = max(self.hwm, equity)
            dd = (self.hwm - equity) / self.hwm if self.hwm > 0 else 0.0
            max_dd = max(max_dd, dd)
            halted = dd >= cfg.drawdown_halt
            if halted:
                halted_days += 1

            # score + blend + trade
            derisk = self._derisk(day, phase)
            focus = set(cfg.focus_symbols)
            convs = []
            for s in cfg.symbols:
                scores = self._scores(s)
                self.score_log.append({"day": day, "symbol": s, "scores": scores})
                present = {k: v for k, v in scores.items() if v is not None}
                convs.append((s, self.conviction.blend(s, present)))
            convs.sort(key=lambda x: -x[1].score)

            if not halted:
                for s, conv in convs:
                    order_value = size_order_value(conv.score, self.closes[s], sizing) * derisk
                    if order_value <= 0:
                        continue
                    side = Side.BUY if conv.score > 0 else Side.SELL
                    if side is Side.BUY and focus and s not in focus:
                        continue
                    px = self.prices[s]
                    if side is Side.BUY:
                        held = self.broker._positions.get(s)
                        held_val = (held.quantity * px) if held else 0.0
                        if held_val + order_value > cfg.max_position_value:
                            continue
                        if order_value > self.broker.cash:
                            continue
                    qty = round(order_value / px, 4)
                    if side is Side.SELL:
                        held = self.broker._positions.get(s)
                        if not held or held.quantity <= 0:
                            continue
                        qty = min(qty, held.quantity)
                    res = self.broker.place_order(Order(symbol=s, side=side, quantity=qty,
                                                        order_type=OrderType.MARKET))
                    if res.status.value == "filled":
                        self.store.record_trade(self.run_id, day, s, side.value, qty, res.filled_price or px,
                                                conv.score, f"{conv.action} conv {conv.score:+.2f}")

            self.store.record_equity(self.run_id, day, equity, self.broker.cash,
                                     equity - self.broker.cash, dd, phase)

            if cfg.self_correct and day % cfg.correct_every == 0 and day > cfg.lookback:
                self._self_correct(day)

            if day % 10 == 0:
                self.store.commit()

        final_equity = self._equity()
        result = SimResult(
            run_id=self.run_id, start_equity=start_equity, final_equity=final_equity,
            total_return=(final_equity / start_equity - 1) if start_equity else 0.0,
            max_drawdown=max_dd, trades=len(self.store.trades(self.run_id, limit=100000)),
            halted_days=halted_days,
            final_weights={s: self.conviction.weights.get(s, 0.0) for s in self.SOURCES},
            source_hit_rates=self.hit_rates(), phases=phase_counts,
        )
        self.store.finish_run(self.run_id, result.to_dict())
        self.store.commit()
        return result

    def _equity(self) -> float:
        acct = self.broker.get_account()
        pos_val = sum(p.quantity * self.prices.get(p.symbol, p.avg_price) for p in acct.positions)
        return acct.cash + pos_val
