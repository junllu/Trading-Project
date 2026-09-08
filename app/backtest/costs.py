"""Transaction costs — because a gross backtest is not a result.

Until now the engine traded at the closing price with no friction of any kind.
`cost_basis` in engine.py is accounting for tax lots, not trading cost. Every
result this project has produced is therefore GROSS, which matters most for
exactly the strategies it was used to evaluate: a weekly-rebalance conviction
model touches the book ~50 times a year, and the setups that looked best were
the ones that traded most.

Three components, kept separate because they scale differently:

    commission   per order, flat. Zero at Robinhood/Webull for equities, but a
                 real number for options ($0.50-0.65 per contract) and the
                 reason a $7 order cap makes contract trading uneconomic.
    spread       half the bid-ask, paid on entry AND on exit. This is the
                 dominant cost for a retail-sized book and it is NOT optional:
                 you buy at the ask and sell at the bid, always.
    slippage     market impact beyond the spread. Negligible at our size in
                 mega-caps, material in thin names, and the term that would
                 explode if this book were 100x larger.

Defaults are deliberately conservative rather than optimistic. The failure mode
being guarded against is a strategy that wins gross and loses net — the single
most common way a backtest lies — so where a value is uncertain the cost is set
high enough that a marginal strategy fails rather than passes.

A note on what this still does NOT model: the close is used as the reference
price, so there is no modelling of intraday timing luck, no partial fills, no
borrow cost for shorts, and no market-on-close auction impact. Costs here are a
floor, not a ceiling.

    python -m app.backtest.costs
"""
from __future__ import annotations

from dataclasses import dataclass

# Measured-ish reference points for a retail book in liquid US equities.
# Spread widens sharply in small caps; these describe the names we actually hold.
MEGA_CAP_SPREAD_BPS = 2.0
LIQUID_SPREAD_BPS = 5.0
THIN_SPREAD_BPS = 25.0


@dataclass(frozen=True)
class CostModel:
    """Round-trip friction. `bps` values are ONE-WAY and charged per fill."""
    commission_per_order: float = 0.0      # equities are commission-free retail
    half_spread_bps: float = LIQUID_SPREAD_BPS / 2
    slippage_bps: float = 1.0

    @property
    def one_way_bps(self) -> float:
        return self.half_spread_bps + self.slippage_bps

    def charge(self, notional: float) -> float:
        """Cost of ONE fill of this size. Always positive."""
        if notional <= 0:
            return 0.0
        return abs(notional) * self.one_way_bps / 10_000.0 + self.commission_per_order

    def effective_buy_price(self, px: float) -> float:
        return px * (1 + self.one_way_bps / 10_000.0)

    def effective_sell_price(self, px: float) -> float:
        return px * (1 - self.one_way_bps / 10_000.0)

    def round_trip_bps(self) -> float:
        return 2 * self.one_way_bps

    def breakeven_move_pct(self) -> float:
        """How far a position must move just to cover getting in and out."""
        return self.round_trip_bps() / 100.0

    @classmethod
    def free(cls) -> "CostModel":
        """Zero friction — ONLY for reproducing historical gross results."""
        return cls(commission_per_order=0.0, half_spread_bps=0.0, slippage_bps=0.0)

    @classmethod
    def retail_equity(cls) -> "CostModel":
        return cls(0.0, LIQUID_SPREAD_BPS / 2, 1.0)

    @classmethod
    def thin_equity(cls) -> "CostModel":
        return cls(0.0, THIN_SPREAD_BPS / 2, 5.0)

    @classmethod
    def retail_option(cls, contracts: int = 1) -> "CostModel":
        # Options spreads are far wider than equities and the per-contract fee
        # is what makes small option trades structurally unprofitable.
        return cls(commission_per_order=0.65 * max(1, contracts),
                   half_spread_bps=150.0, slippage_bps=25.0)


def drag_per_year(model: CostModel, turnover_per_year: float) -> float:
    """Annual % return given up to friction at a given turnover.

    Turnover 1.0 means the book is fully replaced once a year (one round trip).
    This is the number that decides whether a rebalance frequency is affordable,
    and it is linear in turnover — which is why 'rebalance weekly' is a cost
    decision long before it is a signal decision.
    """
    return turnover_per_year * model.round_trip_bps() / 100.0


def _main() -> None:
    print("=" * 70)
    print("  TRANSACTION COSTS — what friction does to a rebalancing strategy")
    print("=" * 70)
    models = [("retail equity (liquid)", CostModel.retail_equity()),
              ("thin equity", CostModel.thin_equity()),
              ("single option contract", CostModel.retail_option(1))]
    for name, m in models:
        print(f"\n  {name}")
        print(f"    one-way {m.one_way_bps:>6.1f} bps   round trip {m.round_trip_bps():>6.1f} bps")
        print(f"    a position must move {m.breakeven_move_pct():.2f}% just to break even")

    print("\n" + "=" * 70)
    print("  ANNUAL DRAG BY REBALANCE FREQUENCY (liquid equities)")
    print("=" * 70)
    m = CostModel.retail_equity()
    for label, turns in [("quarterly", 4), ("monthly", 12), ("weekly", 52), ("daily", 252)]:
        print(f"    {label:<10} turnover {turns:>4}x/yr  ->  {drag_per_year(m, turns):>6.2f}% "
              f"of return given up")
    weekly = drag_per_year(m, 52)
    print(f"\n  The project's best backtested setups rebalanced WEEKLY — {weekly:.2f}% a year")
    print("  of pure friction before any signal has to be right, and every gross")
    print("  result in this repo is overstated by roughly that much. Note the shape:")
    print("  drag is LINEAR in turnover, so rebalance frequency is a cost decision")
    print("  long before it is a signal decision.")


if __name__ == "__main__":
    _main()
