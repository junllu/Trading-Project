"""Backtest the long-call "gem" strategy against an equivalent share allocation.

Reuses the same conviction pipeline as app.backtest.engine (technical +
forecast + macro) to time entries/exits, but instead of buying/selling shares
on a signal, buys a small fixed-dollar pocket of OTM calls and revalues them
via Black-Scholes each rebalance until conviction turns negative or the option
expires, whichever comes first. Answers one question: for the SAME dollars and
the SAME entry/exit timing, would calls or shares have done better?

This is a theoretical-pricing (Black-Scholes) study, not a real fill history —
treat results as directional, not a promise of live premiums.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ..analytics import ConvictionEngine
from ..analytics.technical import technical_score
from ..backtest.data import PriceData
from ..backtest.setups import Setup
from ..macro import MacroEngine
from ..ml import build_forecaster
from .pricing import black_scholes, implied_vol_guess

CAL_PER_TRADING_DAY = 365.0 / 252.0   # convert bar counts to calendar days for BS pricing


@dataclass
class GemOptionTrade:
    symbol: str
    entry_day: int
    exit_day: int
    strike: float
    contracts: int
    premium_paid: float       # total $ paid at entry
    exit_value: float         # total $ realized at exit (0 if expired OTM)
    exit_reason: str          # "conviction_turned" | "expiry" | "run_end"

    @property
    def pnl(self) -> float:
        return self.exit_value - self.premium_paid

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "entry_day": self.entry_day, "exit_day": self.exit_day,
                "strike": self.strike, "contracts": self.contracts,
                "premium_paid": round(self.premium_paid, 2), "exit_value": round(self.exit_value, 2),
                "pnl": round(self.pnl, 2), "exit_reason": self.exit_reason}


@dataclass
class GemBacktestResult:
    trades: list[GemOptionTrade] = field(default_factory=list)
    total_premium_paid: float = 0.0
    total_pnl: float = 0.0
    win_rate: float = 0.0
    share_equivalent_pnl: float = 0.0   # same $, same entry/exit days, but in shares instead

    def to_dict(self) -> dict:
        return {
            "trades": len(self.trades),
            "total_premium_paid": round(self.total_premium_paid, 2),
            "total_pnl": round(self.total_pnl, 2),
            "return_on_premium_pct": round(100 * self.total_pnl / self.total_premium_paid, 1)
            if self.total_premium_paid else 0.0,
            "win_rate_pct": round(self.win_rate * 100, 1),
            "share_equivalent_pnl": round(self.share_equivalent_pnl, 2),
            "edge_vs_shares": round(self.total_pnl - self.share_equivalent_pnl, 2),
        }


def backtest_long_call_gems(setup: Setup, data: PriceData, pocket_size: float = 1000.0,
                            otm_pct: float = 0.08, expiry_calendar_days: int = 35,
                            warmup: int = 35) -> GemBacktestResult:
    conviction = ConvictionEngine(setup.weights)
    forecaster = build_forecaster(setup.forecast_model)
    macro = MacroEngine()
    expiry_bars = max(1, round(expiry_calendar_days / CAL_PER_TRADING_DAY))

    open_pos: dict[str, dict] = {}
    trades: list[GemOptionTrade] = []
    share_pnl_total = 0.0
    n = len(data)

    def _close(s: str, t: int, spot: float, reason: str) -> None:
        nonlocal share_pnl_total
        pos = open_pos.pop(s)
        bars_left = max(pos["expiry_bar"] - t, 0)
        iv = implied_vol_guess(s)
        g = black_scholes(spot, pos["strike"], bars_left * CAL_PER_TRADING_DAY, iv, is_call=True)
        exit_value = g.price * 100 * pos["contracts"]
        trades.append(GemOptionTrade(
            symbol=s, entry_day=pos["entry_day"], exit_day=t, strike=pos["strike"],
            contracts=pos["contracts"], premium_paid=pos["premium_paid"],
            exit_value=exit_value, exit_reason=reason,
        ))
        share_pnl_total += pos["premium_paid"] * (spot / pos["entry_spot"] - 1.0)

    for t in range(warmup, n, setup.rebalance_days):
        for s in data.symbols:
            hist = data.closes[s][: t + 1]
            spot = data.closes[s][t]
            tech = technical_score(s, hist)
            fc = forecaster.predict(s, hist)
            inputs: dict[str, float] = {}
            if "technical" in setup.weights and tech.ready:
                inputs["technical"] = tech.score
            if "forecast" in setup.weights and fc.confidence > 0:
                inputs["forecast"] = fc.score()
            if "macro" in setup.weights:
                mb = macro.symbol_bias(s, data.dates[t])
                if abs(mb) > 0.02:
                    inputs["macro"] = mb
            conv = conviction.blend(s, inputs)

            pos = open_pos.get(s)
            if pos is not None:
                expired = t >= pos["expiry_bar"]
                turned = conv.score < 0
                if expired or turned:
                    _close(s, t, spot, "expiry" if expired else "conviction_turned")
                    pos = None

            if pos is None and conv.score >= setup.entry_threshold:
                strike = round(spot * (1 + otm_pct), 2)
                iv = implied_vol_guess(s)
                g = black_scholes(spot, strike, expiry_calendar_days, iv, is_call=True)
                premium_per_contract = g.price * 100
                if premium_per_contract <= 0 or premium_per_contract > pocket_size:
                    continue                              # can't afford even 1 contract
                contracts = max(1, int(pocket_size // premium_per_contract))
                open_pos[s] = {
                    "entry_day": t, "strike": strike, "contracts": contracts,
                    "premium_paid": premium_per_contract * contracts,
                    "expiry_bar": t + expiry_bars, "entry_spot": spot,
                }

    for s in list(open_pos):
        _close(s, n - 1, data.closes[s][n - 1], "run_end")

    total_premium = sum(tr.premium_paid for tr in trades)
    total_pnl = sum(tr.pnl for tr in trades)
    wins = sum(1 for tr in trades if tr.pnl > 0)
    return GemBacktestResult(
        trades=trades, total_premium_paid=total_premium, total_pnl=total_pnl,
        win_rate=(wins / len(trades) if trades else 0.0), share_equivalent_pnl=share_pnl_total,
    )
