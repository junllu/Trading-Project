"""Tiered "pockets" allocation — a conviction barbell.

You defined the sleeves: a few large pockets for high-conviction core positions
and several tiny pockets for asymmetric "gem" bets. Example $80k ladder:

    [30000, 20000, 10000, 10000, 5000, 1000, 1000, 1000, 1000, 1000]

The allocator ranks candidates and fills the ladder risk-adjusted:

  * CORE (the large pockets) go to the highest **risk-adjusted conviction** names
    — real capital only where the edge is strongest and the risk is contained.
  * GEMS (the $1k pockets) go to the highest **upside** names that didn't make
    the core — small size caps the loss so you can hold a few lottery tickets
    without endangering the mission. This is how you "find a couple gems"
    without chasing hype: the hype names get $1k, not $20k.

Unfilled pockets stay in cash — the allocator never force-buys a weak name.
"""
from __future__ import annotations

from dataclasses import dataclass, field

DEFAULT_POCKETS = [30000, 20000, 10000, 10000, 5000, 1000, 1000, 1000, 1000, 1000]


@dataclass
class Candidate:
    symbol: str
    conviction: float          # -1..1 blended conviction
    upside: float              # 0..1 expected upside magnitude (e.g. forecast return)
    risk: float = 0.3          # 0..1 (e.g. normalized volatility)

    def risk_adjusted(self) -> float:
        # penalize conviction by risk so core capital favors contained-risk edges
        return self.conviction * (1.0 - 0.4 * max(0.0, min(1.0, self.risk)))


@dataclass
class Allocation:
    symbol: str
    target_value: float
    sleeve: str                # "core" | "gem"
    conviction: float
    upside: float
    rationale: str = ""

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "target_value": round(self.target_value, 2),
                "sleeve": self.sleeve, "conviction": round(self.conviction, 3),
                "upside": round(self.upside, 3), "rationale": self.rationale}


@dataclass
class AllocationPlan:
    total: float
    allocations: list[Allocation] = field(default_factory=list)
    cash_unallocated: float = 0.0

    def to_dict(self) -> dict:
        return {
            "total": round(self.total, 2),
            "invested": round(self.total - self.cash_unallocated, 2),
            "cash_unallocated": round(self.cash_unallocated, 2),
            "core": [a.to_dict() for a in self.allocations if a.sleeve == "core"],
            "gems": [a.to_dict() for a in self.allocations if a.sleeve == "gem"],
        }


class PocketAllocator:
    def __init__(self, pockets: list[float] | None = None, total: float | None = None,
                 gem_threshold: float = 2000.0, min_core_conviction: float = 0.15,
                 min_gem_upside: float = 0.03):
        self.pockets = sorted(pockets or DEFAULT_POCKETS, reverse=True)
        self.total = total if total is not None else float(sum(self.pockets))
        self.gem_threshold = gem_threshold
        self.min_core_conviction = min_core_conviction
        self.min_gem_upside = min_gem_upside

    def allocate(self, candidates: list[Candidate]) -> AllocationPlan:
        core_pockets = [p for p in self.pockets if p >= self.gem_threshold]
        gem_pockets = [p for p in self.pockets if p < self.gem_threshold]
        used: set[str] = set()
        allocations: list[Allocation] = []

        # CORE — highest risk-adjusted conviction (long only).
        core_ranked = sorted(
            [c for c in candidates if c.conviction >= self.min_core_conviction],
            key=lambda c: -c.risk_adjusted(),
        )
        ci = 0
        for pocket in core_pockets:
            while ci < len(core_ranked) and core_ranked[ci].symbol in used:
                ci += 1
            if ci >= len(core_ranked):
                break                                    # no more worthy core names -> cash
            c = core_ranked[ci]; ci += 1; used.add(c.symbol)
            allocations.append(Allocation(
                c.symbol, pocket, "core", c.conviction, c.upside,
                f"conviction {c.conviction:+.2f}, risk {c.risk:.2f}",
            ))

        # GEMS — highest upside among the rest (asymmetric, small size).
        gem_ranked = sorted(
            [c for c in candidates if c.symbol not in used and c.upside >= self.min_gem_upside],
            key=lambda c: -c.upside,
        )
        gi = 0
        for pocket in gem_pockets:
            if gi >= len(gem_ranked):
                break
            c = gem_ranked[gi]; gi += 1; used.add(c.symbol)
            allocations.append(Allocation(
                c.symbol, pocket, "gem", c.conviction, c.upside,
                f"upside {c.upside:+.1%}, small asymmetric bet",
            ))

        invested = sum(a.target_value for a in allocations)
        return AllocationPlan(total=self.total, allocations=allocations,
                              cash_unallocated=max(0.0, self.total - invested))
