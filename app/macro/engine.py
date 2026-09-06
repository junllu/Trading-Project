"""MacroEngine — resolve a date to the active policy regimes and a symbol tilt.

Given a date (real, YYYY-MM-DD), it finds every overlapping regime, sums their
sector biases (administrations weighted lighter than sharp policy/macro events),
and maps the result to a per-symbol score in [-1, 1] via the ticker->sector map.
That score plugs into the conviction blend as the `macro` source, and the
backtest uses the *replay* date so history is analyzed in its own policy weather.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from .timeline import REGIMES, MacroRegime, sector_of

# Sharp, time-boxed events move a sector more than a 4-year administration prior.
KIND_WEIGHT = {"administration": 0.6, "policy": 1.0, "macro": 1.0}


def _d(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


@dataclass
class MacroView:
    as_of: str
    regimes: list[str] = field(default_factory=list)          # active regime names
    sector_bias: dict[str, float] = field(default_factory=dict)
    rationale: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "as_of": self.as_of,
            "regimes": self.regimes,
            "sector_bias": {k: round(v, 2) for k, v in
                            sorted(self.sector_bias.items(), key=lambda x: -abs(x[1]))},
            "rationale": self.rationale,
        }


class MacroEngine:
    def __init__(self, regimes: list[MacroRegime] | None = None):
        self.regimes = regimes or REGIMES

    def _active(self, on: date) -> list[MacroRegime]:
        out = []
        for r in self.regimes:
            try:
                if _d(r.start) <= on <= _d(r.end):
                    out.append(r)
            except ValueError:
                continue
        return out

    def view(self, as_of: str | None = None) -> MacroView:
        if as_of:
            try:
                on = _d(as_of)
            except ValueError:
                return MacroView(as_of=as_of)     # non-real date (e.g. synthetic) -> no macro
        else:
            on = date.today()
        active = self._active(on)
        sector: dict[str, float] = {}
        for r in active:
            w = KIND_WEIGHT.get(r.kind, 1.0)
            for sec, b in r.sector_bias.items():
                sector[sec] = sector.get(sec, 0.0) + b * w
        # squash to [-1, 1]
        sector = {k: max(-1.0, min(1.0, v)) for k, v in sector.items()}
        v = MacroView(as_of=on.isoformat(), regimes=[r.name for r in active], sector_bias=sector)
        v.rationale = [f"{r.name}: {r.description}" for r in active if r.kind != "administration"] \
            or [f"{r.name}: {r.description}" for r in active]
        return v

    def symbol_bias(self, symbol: str, as_of: str | None = None) -> float:
        """Macro score for one symbol on a date, in [-1, 1]."""
        return self.view(as_of).sector_bias.get(sector_of(symbol), 0.0)

    def symbol_biases(self, symbols: list[str], as_of: str | None = None) -> dict[str, float]:
        v = self.view(as_of)
        return {s: v.sector_bias.get(sector_of(s), 0.0) for s in symbols}
