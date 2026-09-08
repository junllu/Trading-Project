"""Coordinated options program across both capital pools.

The three option strategies are not independent — run naively they fight each
other and the core thesis:

  1. A covered call caps upside on exactly the position whose upside the
     buy-and-hold thesis depends on. Writing $220 calls on MRVL while the
     campaign is betting on MRVL into 2027 sells the thesis for premium.
  2. Assignment DESTROYS a core hold. The trade-history analysis showed the
     costly historical pattern was selling winners early (MU exited at $80.75,
     now $1,016 — 12.6x forgone). Assignment is that mistake, automated.
  3. A long call and a short call on the same name are self-cancelling unless
     deliberately structured as a spread.
  4. A cash-secured put ties up collateral the options sleeve may need elsewhere,
     and only makes sense on names you actually want to own — i.e. names that
     pass the CORE screen, not merely the options screen.

So this module coordinates rather than generating each in isolation:

  covered calls  -> only on lots NOT in the core thesis, or struck above the
                    thesis target so assignment is an acceptable outcome
  cash-secured   -> only on core-screen names, sized to real settled cash
  puts              (entry points: get paid to wait for a price you'd buy at)
  long calls     -> only on names with no short call open (no self-cancelling)

Everything returns PLANS for review. Nothing places orders.

    python -m app.options.program
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field

MIN_SHARES_PER_CONTRACT = 100


@dataclass
class Conflict:
    symbol: str
    kind: str
    detail: str

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "kind": self.kind, "detail": self.detail}


@dataclass
class ProgramPlan:
    covered_calls: list = field(default_factory=list)
    cash_secured_puts: list = field(default_factory=list)
    long_call_candidates: list = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "covered_calls": [p.to_dict() for p in self.covered_calls],
            "cash_secured_puts": [p.to_dict() for p in self.cash_secured_puts],
            "long_call_candidates": self.long_call_candidates,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "notes": self.notes,
        }


def _lots_by_account(holdings: list[dict]) -> dict[tuple[str, str], float]:
    """Share counts keyed by (symbol, broker) — lots do NOT merge across brokers."""
    out: dict[tuple[str, str], float] = {}
    for h in holdings:
        key = (str(h["symbol"]).upper(), str(h.get("broker", "unknown")))
        out[key] = out.get(key, 0.0) + float(h["shares"])
    return out


def build_program(holdings: list[dict], prices: dict[str, float], cash: float,
                  core_thesis: set[str], days: int = 35,
                  cc_otm_pct: float = 0.10, csp_otm_pct: float = 0.07) -> ProgramPlan:
    from .strategies import cash_secured_put_candidates, covered_call_candidates
    from ..models import Position

    plan = ProgramPlan()
    by_acct = _lots_by_account(holdings)

    # --- covered calls: eligible lots, excluding core-thesis names ----------
    writable: list[Position] = []
    for (sym, broker), qty in sorted(by_acct.items()):
        if qty < MIN_SHARES_PER_CONTRACT:
            continue
        if sym in core_thesis:
            plan.conflicts.append(Conflict(
                sym, "covered_call_blocked",
                f"{int(qty)} sh in {broker} could write {int(qty // 100)} call(s), but {sym} is a "
                f"CORE THESIS hold — assignment would sell the position the campaign depends on"))
            continue
        avg = next((float(h.get("avg_price", 0)) for h in holdings
                    if str(h["symbol"]).upper() == sym), 0.0)
        writable.append(Position(symbol=sym, quantity=qty, avg_price=avg, broker=broker))

    if writable:
        plan.covered_calls = covered_call_candidates(writable, prices, days=days,
                                                     otm_pct=cc_otm_pct)

    # --- cash-secured puts: only names you'd genuinely want to own ----------
    csp_universe = sorted(core_thesis)
    affordable = []
    for sym in csp_universe:
        spot = prices.get(sym, 0.0)
        if spot <= 0:
            continue
        collateral = spot * (1 - csp_otm_pct) * 100
        if collateral <= cash:
            affordable.append(sym)
        else:
            plan.conflicts.append(Conflict(
                sym, "csp_unaffordable",
                f"a {csp_otm_pct:.0%} OTM put needs ~${collateral:,.0f} collateral; "
                f"available cash is ${cash:,.0f}"))
    if affordable:
        plan.cash_secured_puts = cash_secured_put_candidates(affordable, prices, cash,
                                                             days=days, otm_pct=csp_otm_pct)

    # --- long calls: block names already carrying a short call --------------
    shorted = {p.symbol for p in plan.covered_calls}
    for sym in sorted(core_thesis):
        if sym in shorted:
            plan.conflicts.append(Conflict(
                sym, "long_and_short_same_name",
                "a covered call is proposed here — buying calls too would be self-cancelling"))
            continue
        plan.long_call_candidates.append(sym)

    if not plan.covered_calls and not plan.cash_secured_puts:
        plan.notes.append("No income legs available: eligible lots are all core-thesis holds, "
                          "and cash is short of any CSP collateral requirement.")
    plan.notes.append("Covered calls and CSPs must be placed IN THE ACCOUNT HOLDING THE SHARES "
                      "or CASH. The agentic account (option_level_3) holds neither.")
    return plan


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=35)
    ap.add_argument("--cc-otm", type=float, default=0.10)
    ap.add_argument("--csp-otm", type=float, default=0.07)
    args = ap.parse_args()

    from ..portfolio.holdings import load_cash, load_holdings
    holdings = load_holdings()
    prices = {str(h["symbol"]).upper(): float(h.get("last", h.get("avg_price", 0)))
              for h in holdings}
    cash = load_cash() or 0.0

    # Core thesis = campaign focus + the AI-stack names the sources flagged.
    core = {"MRVL", "NVDA", "TSLA", "MU", "SNDK", "ALAB", "ANET", "AEIS", "BE"}
    core &= (set(prices) | {"MU", "SNDK"})

    plan = build_program(holdings, prices, cash, core, days=args.days,
                         cc_otm_pct=args.cc_otm, csp_otm_pct=args.csp_otm)

    print("=" * 74)
    print("COORDINATED OPTIONS PROGRAM")
    print("=" * 74)
    print(f"\nCOVERED CALLS ({len(plan.covered_calls)}):")
    for p in plan.covered_calls or []:
        print(f"  {p.action}")
        print(f"     credit ${p.est_premium:,.0f}  annualized {p.annualized_return:.1%}")
        for w in p.warnings:
            print(f"     ! {w}")
    if not plan.covered_calls:
        print("  none")

    print(f"\nCASH-SECURED PUTS ({len(plan.cash_secured_puts)}):")
    for p in plan.cash_secured_puts or []:
        print(f"  {p.action}   credit ${p.est_premium:,.0f}")
    if not plan.cash_secured_puts:
        print("  none")

    print(f"\nLONG-CALL CANDIDATES (no conflicting short call): "
          f"{', '.join(plan.long_call_candidates) or 'none'}")

    print(f"\nCONFLICTS BLOCKED ({len(plan.conflicts)}):")
    for c in plan.conflicts:
        print(f"  [{c.kind}] {c.symbol}: {c.detail}")

    print("\nNOTES:")
    for n in plan.notes:
        print(f"  - {n}")


if __name__ == "__main__":
    _main()
