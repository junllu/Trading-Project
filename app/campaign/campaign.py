"""The Campaign engine — the mission, made operational.

Goal: grow the existing capital toward a target ($1,000,000) by a deadline
(end-2027, before the anticipated 2028 semiconductor down-cycle), concentrated
in a few blue-chip volatile cycle plays (MRVL, NVDA, TSLA), WITHOUT giving the
gains back. It encodes three disciplines:

  1. Progress & pace   — where equity stands vs the exponential glide path to
                         the target, and the return still required.
  2. Capital preservation — a high-water-mark trailing drawdown halt: if the
                         book falls more than `trailing_drawdown_halt` from its
                         peak, the campaign says STOP (the engine trips the
                         kill-switch). This is the operational "make no mistake."
  3. Exit clock        — as the deadline nears, a de-risk factor ramps risk
                         down, so we scale out into the up-cycle instead of
                         round-tripping into the downturn.

Nothing here promises the target — it enforces the discipline that gives the
best survivable shot at it.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime


def _parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


@dataclass
class CampaignStatus:
    equity: float
    target: float
    progress: float                  # equity/target, 0..1+
    multiple_remaining: float        # target/equity
    high_water_mark: float
    drawdown: float                  # fraction below HWM (0..1)
    drawdown_halt: float
    breached: bool                   # drawdown exceeded the halt
    days_remaining: int
    required_cagr: float             # annualized return still needed
    required_total_return: float     # total multiple still needed - 1
    pace_ratio: float                # equity / glide-path expectation (>1 ahead)
    pace: str                        # "ahead" | "on_track" | "behind"
    derisk_factor: float             # 1.0 early -> lower near the deadline
    phase: str                       # "accumulate" | "de-risk" | "exit"
    focus_symbols: list[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "equity": round(self.equity, 2),
            "target": self.target,
            "progress": round(self.progress, 4),
            "progress_pct": round(self.progress * 100, 1),
            "multiple_remaining": round(self.multiple_remaining, 2),
            "high_water_mark": round(self.high_water_mark, 2),
            "drawdown": round(self.drawdown, 4),
            "drawdown_pct": round(self.drawdown * 100, 1),
            "drawdown_halt_pct": round(self.drawdown_halt * 100, 1),
            "breached": self.breached,
            "days_remaining": self.days_remaining,
            "required_cagr_pct": round(self.required_cagr * 100, 1),
            "required_total_return_pct": round(self.required_total_return * 100, 1),
            "pace_ratio": round(self.pace_ratio, 3),
            "pace": self.pace,
            "derisk_factor": round(self.derisk_factor, 3),
            "phase": self.phase,
            "focus_symbols": self.focus_symbols,
            "note": self.note,
        }


@dataclass
class Campaign:
    start_capital: float
    target: float = 1_000_000.0
    started: str = "2026-09-05"
    deadline: str = "2027-12-31"
    focus_symbols: list[str] = field(default_factory=lambda: ["MRVL", "NVDA", "TSLA"])
    trailing_drawdown_halt: float = 0.20         # halt if >20% below high-water mark
    derisk_window_days: int = 210                # start scaling out ~7 months before deadline
    derisk_floor: float = 0.2                    # risk budget floor at the deadline
    high_water_mark: float = 0.0

    # --- high-water mark / drawdown ---------------------------------------
    def update_hwm(self, equity: float) -> None:
        if equity > self.high_water_mark:
            self.high_water_mark = equity

    def drawdown(self, equity: float) -> float:
        if self.high_water_mark <= 0:
            return 0.0
        return max(0.0, (self.high_water_mark - equity) / self.high_water_mark)

    def breached(self, equity: float) -> bool:
        return self.drawdown(equity) >= self.trailing_drawdown_halt

    # --- time / pace -------------------------------------------------------
    def days_remaining(self, now: date | None = None) -> int:
        now = now or date.today()
        return (_parse_date(self.deadline) - now).days

    def _years_remaining(self, now: date | None = None) -> float:
        return max(self.days_remaining(now), 1) / 365.0

    def required_total_return(self, equity: float) -> float:
        if equity <= 0:
            return float("inf")
        return max(0.0, self.target / equity - 1.0)

    def required_cagr(self, equity: float, now: date | None = None) -> float:
        if equity <= 0 or equity >= self.target:
            return 0.0
        yrs = self._years_remaining(now)
        return (self.target / equity) ** (1.0 / yrs) - 1.0

    def expected_equity(self, now: date | None = None) -> float:
        """Point on the exponential glide path from start_capital -> target."""
        start_d, end_d = _parse_date(self.started), _parse_date(self.deadline)
        now = now or date.today()
        total = max((end_d - start_d).days, 1)
        elapsed = min(max((now - start_d).days, 0), total)
        frac = elapsed / total
        if self.start_capital <= 0:
            return self.target * frac
        return self.start_capital * (self.target / self.start_capital) ** frac

    def pace_ratio(self, equity: float, now: date | None = None) -> float:
        exp = self.expected_equity(now)
        return equity / exp if exp > 0 else 1.0

    # --- exit clock / de-risk ---------------------------------------------
    def derisk_factor(self, now: date | None = None) -> float:
        d = self.days_remaining(now)
        if d <= 0:
            return self.derisk_floor
        if d >= self.derisk_window_days:
            return 1.0
        frac = d / self.derisk_window_days                # 1 far out -> 0 at deadline
        return self.derisk_floor + (1.0 - self.derisk_floor) * frac

    def phase(self, now: date | None = None) -> str:
        d = self.days_remaining(now)
        if d <= 30:
            return "exit"
        if d <= self.derisk_window_days:
            return "de-risk"
        return "accumulate"

    # --- rollup ------------------------------------------------------------
    def status(self, equity: float, now: date | None = None) -> CampaignStatus:
        self.update_hwm(equity)
        dd = self.drawdown(equity)
        breached = dd >= self.trailing_drawdown_halt
        pr = self.pace_ratio(equity, now)
        pace = "ahead" if pr >= 1.1 else "behind" if pr <= 0.9 else "on_track"
        st = CampaignStatus(
            equity=equity, target=self.target,
            progress=equity / self.target if self.target else 0.0,
            multiple_remaining=self.target / equity if equity > 0 else float("inf"),
            high_water_mark=self.high_water_mark, drawdown=dd,
            drawdown_halt=self.trailing_drawdown_halt, breached=breached,
            days_remaining=self.days_remaining(now),
            required_cagr=self.required_cagr(equity, now),
            required_total_return=self.required_total_return(equity),
            pace_ratio=pr, pace=pace,
            derisk_factor=self.derisk_factor(now), phase=self.phase(now),
            focus_symbols=list(self.focus_symbols),
        )
        st.note = self._note(st)
        return st

    @staticmethod
    def _note(st: CampaignStatus) -> str:
        if st.breached:
            return (f"⛔ DRAWDOWN HALT: book is {st.drawdown * 100:.0f}% below its peak "
                    f"(limit {st.drawdown_halt * 100:.0f}%). Preserve capital — trading halted.")
        parts = [f"{st.progress * 100:.0f}% of the way to ${st.target:,.0f}"]
        parts.append(f"needs {st.required_cagr * 100:.0f}%/yr for {st.days_remaining} more days")
        parts.append(f"pace: {st.pace}")
        if st.phase != "accumulate":
            parts.append(f"phase: {st.phase} (risk x{st.derisk_factor:.2f})")
        return " · ".join(parts)
