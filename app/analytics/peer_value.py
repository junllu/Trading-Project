"""Relative valuation — a multiple only means something against the alternatives.

The operator's primary screen, described in their own words: look at the P/E
against competitors in the same market, then sentiment, then whether the growth
is there for the next few years. The system had the third of those and nothing
at all of the first.

That absence produced a specific blind spot. The thesis ledger reads multiples
ABSOLUTELY — "MU at 23x is cheap", "BE at 344x is expensive" — which sounds like
valuation but is not. 23x is cheap for software and expensive for a memory maker
at peak margin; 63x is dear for an industrial and ordinary for a compounder. The
only question that survives contact with a real decision is: cheap or dear
COMPARED TO WHAT, and am I holding the cheap one or the dear one?

Two things this deliberately refuses to do:

  A LOW MULTIPLE IS NOT A BUY SIGNAL. It is the start of a question. Memory
  trades at 23x precisely because the market expects those earnings to fall,
  which app/intel/filings.py already showed is correct — 97% of MU's revenue
  growth is price. Cheap and correctly cheap look identical on this screen.

  A NEGATIVE P/E IS NOT A CHEAP ONE. Loss-making names are excluded from the
  percentile maths and reported separately, because sorting them numerically
  would rank the worst business in the group as the best value in it.

Peer membership is a judgement, and it is the whole answer here. BE against
fuel-cell startups looks visionary; against GEV, ETN and PWR — the companies
actually competing to power a datacenter — it is 5 to 12 times its peer group.
Groups are chosen to reflect who competes for the same dollar.

    python -m app.analytics.peer_value
    python -m app.analytics.peer_value --symbol BE
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from dataclasses import dataclass, field

from ..config import ROOT

FUNDAMENTALS_DIR = ROOT / "data" / "fundamentals"

CHEAPEST, CHEAP, MID, RICH, RICHEST = (
    "CHEAPEST", "BELOW PEERS", "IN LINE", "ABOVE PEERS", "MOST EXPENSIVE")


@dataclass
class PeerStanding:
    symbol: str
    group: str
    pe: float
    pb: float
    mcap_b: float
    held: bool
    rank: int                    # 1 = cheapest on P/E
    of: int
    percentile: float            # 0 = cheapest, 100 = dearest
    group_median_pe: float
    vs_median_x: float           # multiple of the group median
    band: str
    note: str = ""

    def to_dict(self) -> dict:
        return dict(self.__dict__)


@dataclass
class GroupView:
    name: str
    label: str
    standings: list[PeerStanding] = field(default_factory=list)
    loss_making: list[str] = field(default_factory=list)
    median_pe: float = 0.0


def latest_snapshot() -> tuple[dict, str] | tuple[None, None]:
    files = sorted(FUNDAMENTALS_DIR.glob("peers_*.json")) if FUNDAMENTALS_DIR.exists() else []
    if not files:
        return None, None
    return json.loads(files[-1].read_text(encoding="utf-8")), \
        files[-1].stem.replace("peers_", "")


def _band(pct: float, rank: int, of: int) -> str:
    if rank == 1:
        return CHEAPEST
    if rank == of:
        return RICHEST
    if pct <= 33:
        return CHEAP
    if pct >= 67:
        return RICH
    return MID


def build() -> list[GroupView]:
    snap, _ = latest_snapshot()
    if snap is None:
        return []
    out: list[GroupView] = []
    for gname, g in snap["groups"].items():
        gv = GroupView(name=gname, label=g.get("label", gname))
        # Loss-makers are set aside, not ranked. A -45 P/E sorted numerically
        # would come out "cheapest" in the group, which is the opposite of true.
        priced = {s: v for s, v in g["members"].items() if v.get("pe", 0) > 0}
        gv.loss_making = sorted(s for s, v in g["members"].items() if v.get("pe", 0) <= 0)
        if len(priced) < 2:
            out.append(gv)
            continue

        gv.median_pe = round(st.median(v["pe"] for v in priced.values()), 2)
        ordered = sorted(priced.items(), key=lambda kv: kv[1]["pe"])
        n = len(ordered)
        for i, (sym, v) in enumerate(ordered, start=1):
            pct = round(100 * (i - 1) / (n - 1), 1) if n > 1 else 0.0
            gv.standings.append(PeerStanding(
                symbol=sym, group=gname, pe=v["pe"], pb=v.get("pb", 0.0),
                mcap_b=v.get("mcap_b", 0.0), held=bool(v.get("held")),
                rank=i, of=n, percentile=pct, group_median_pe=gv.median_pe,
                vs_median_x=round(v["pe"] / gv.median_pe, 2) if gv.median_pe else 0.0,
                band=_band(pct, i, n)))
        out.append(gv)
    return out


def report() -> dict:
    groups = build()
    held = [s for g in groups for s in g.standings if s.held]
    dear = [s for s in held if s.band in (RICH, RICHEST)]
    return {
        "groups": {g.name: {"label": g.label, "median_pe": g.median_pe,
                            "loss_making": g.loss_making,
                            "standings": [s.to_dict() for s in g.standings]}
                   for g in groups},
        "held_count": len(held),
        "held_above_peers": [s.symbol for s in dear],
        "note": ("A low multiple is not a buy signal — it is the start of a question. "
                 "Cheap and correctly-cheap look identical on this screen."),
    }


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.json:
        print(json.dumps(report(), indent=2))
        return

    groups = build()
    if not groups:
        print("no peer snapshot in data/fundamentals/peers_*.json")
        return

    _, snap_date = latest_snapshot()
    print("=" * 78)
    print("  RELATIVE VALUATION — cheap or dear COMPARED TO WHAT?")
    print("=" * 78)
    print(f"  peers as of {snap_date}\n")

    for g in groups:
        if args.symbol and not any(s.symbol == args.symbol.upper() for s in g.standings):
            continue
        print(f"  {g.label}   (median P/E {g.median_pe})")
        print("  " + "-" * 74)
        for s in g.standings:
            mark = " <-- HELD" if s.held else ""
            print(f"    {s.rank:>2}/{s.of}  {s.symbol:6} P/E {s.pe:>7.1f}  "
                  f"{s.vs_median_x:>5.2f}x median   P/B {s.pb:>6.1f}   "
                  f"${s.mcap_b:>6,.0f}B   {s.band:<15}{mark}")
        if g.loss_making:
            print(f"         loss-making, not ranked: {', '.join(g.loss_making)}")
        print()

    held = [s for g in groups for s in g.standings if s.held]
    dear = [s for s in held if s.band in (RICH, RICHEST)]
    cheap = [s for s in held if s.band in (CHEAP, CHEAPEST)]
    print("=" * 78)
    print(f"  HOLDINGS ON YOUR PRIMARY SCREEN — {len(held)} priced")
    print(f"    above peers : {', '.join(s.symbol for s in dear) or 'none'}")
    print(f"    below peers : {', '.join(s.symbol for s in cheap) or 'none'}")
    if dear:
        worst = max(dear, key=lambda s: s.vs_median_x)
        print(f"\n    widest gap  : {worst.symbol} at {worst.vs_median_x:.1f}x its group "
              f"median ({worst.pe:.0f} vs {worst.group_median_pe:.0f})")
    print("\n    This screen says nothing about whether the premium is deserved —")
    print("    only that it is being paid. The thesis ledger owns the other half.")

    # The cross-reference is where the two screens become a decision. A premium
    # multiple is fine when the business is compounding; it is a different
    # proposition when the filings show costs outrunning revenue at the same
    # time. Neither screen says this alone.
    try:
        from ..intel.filings import report as filings_report
        chars = {r["op"].symbol: r["op"] for r in filings_report().get("rows", [])}
    except Exception:
        chars = {}
    both = [(s, chars[s.symbol]) for s in dear if s.symbol in chars
            and chars[s.symbol].character == "MARGIN_PRESSURE"]
    if both:
        print(f"\n  {'=' * 74}")
        print("  PAYING A PREMIUM WHILE THE OPERATIONS DETERIORATE")
        print("  (dear vs peers AND costs outrunning revenue in the latest filing)")
        for s, op in sorted(both, key=lambda x: -x[0].vs_median_x):
            print(f"    {s.symbol:6} {s.vs_median_x:>5.2f}x peer median   "
                  f"gross margin {op.margin_delta_pp:>+5.1f}pp   "
                  f"cost elasticity {op.cost_elasticity}")
        print("\n    Each of these is a judgement call, not a verdict — but it is the")
        print("    specific call worth making deliberately rather than by default.")


if __name__ == "__main__":
    _main()
