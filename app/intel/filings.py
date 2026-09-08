"""Operational metrics from primary SEC filings — the CORE leg's research layer.

This exists to answer one question the income statement cannot: when revenue
grows, is that because the company sold MORE, or because it charged MORE?

The two look identical on a revenue line and mean opposite things for a holding
underwritten to 2028. Volume growth compounds. Price growth mean-reverts, and it
reverts against a cost base that did not fall — which is why memory cycles
collapse earnings by 80% instead of 20%.

The discriminator is cost elasticity:

    cost_elasticity = COGS growth % / revenue growth %

    ~0.0   revenue rose, costs did not. Almost all price. Cyclical, reverting.
    ~1.0   costs scaled with revenue. Volume. Durable but no leverage.
    <1.0 with margin expansion  volume PLUS operating leverage. The best shape.
    >1.0   costs outran revenue. Buying share with price. Margin is going.

MU is the textbook case: +346% revenue on +10.5% cost, an elasticity of 0.03.
Roughly 97% of that revenue increase is price. It is completely real today and
completely temporary, and the arithmetic says so without any judgement call.

Second metric, inventory days:

    inventory_days = inventory / COGS * days_in_period

Falling days into a price spike corroborates a genuine shortage. RISING days
into a price spike is the classic distributor build — inventory accumulating at
peak prices, which is what gets written down on the way back. Same headline,
opposite conclusion, and only the filing distinguishes them.

Everything is computed from XBRL facts in a dated snapshot (data/fundamentals/
filings_*.json) pulled through the broker MCP. Nothing here is a vendor's
summary or an LLM's reading of a document; every number traces to a tagged fact.

    python -m app.intel.filings
    python -m app.intel.filings --symbol ANET
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import date

from ..config import ROOT

FUNDAMENTALS_DIR = ROOT / "data" / "fundamentals"

# Elasticity bands. Set from economics, not fitted: 0.25 means three-quarters of
# revenue growth came from price, which no durable business sustains.
PRICE_DRIVEN = 0.25
COST_OUTRUNNING = 1.10

PRICE_SPIKE, VOLUME_LEVERAGE, VOLUME, MARGIN_PRESSURE, FLAT = (
    "PRICE_SPIKE", "VOLUME+LEVERAGE", "VOLUME", "MARGIN_PRESSURE", "FLAT")


@dataclass
class Operating:
    symbol: str
    basis: str
    filing: str
    rev_growth_pct: float
    cogs_growth_pct: float
    gross_margin_pct: float
    gross_margin_prior_pct: float
    inventory_days: float | None
    inventory_days_prior: float | None
    inventory_change_pct: float
    cost_elasticity: float | None
    character: str
    price_share_pct: float | None      # share of revenue growth attributable to price
    flags: list[str]

    def to_dict(self) -> dict:
        return dict(self.__dict__)

    @property
    def margin_delta_pp(self) -> float:
        return self.gross_margin_pct - self.gross_margin_prior_pct


def _pct(new: float, old: float) -> float:
    return (new / old - 1) * 100 if old else 0.0


def analyse(sym: str, e: dict) -> Operating:
    rev_g = _pct(e["revenue_current"], e["revenue_prior"])
    cogs_g = _pct(e["cogs_current"], e["cogs_prior"])
    gm = (1 - e["cogs_current"] / e["revenue_current"]) * 100
    gm_prior = (1 - e["cogs_prior"] / e["revenue_prior"]) * 100

    days = e.get("days_in_period", 91)
    inv_d = (e["inventory_current"] / e["cogs_current"] * days) if e.get("inventory_current") else None
    inv_dp = (e["inventory_prior"] / e["cogs_prior"] * days) if e.get("inventory_prior") else None
    inv_chg = _pct(e.get("inventory_current", 0), e.get("inventory_prior", 0))

    # Elasticity is only meaningful when revenue actually grew.
    elas = (cogs_g / rev_g) if rev_g > 1.0 else None

    flags: list[str] = []
    if elas is None:
        character = FLAT
        price_share = None
    elif elas <= PRICE_DRIVEN:
        character = PRICE_SPIKE
        price_share = round((1 - elas) * 100, 1)
        flags.append(
            f"{price_share:.0f}% of revenue growth is PRICE, not volume — this "
            f"reverts against a cost base that will not fall with it")
    elif elas >= COST_OUTRUNNING:
        character = MARGIN_PRESSURE
        price_share = None
        flags.append("costs grew FASTER than revenue — share is being bought with price")
    elif gm > gm_prior:
        character = VOLUME_LEVERAGE
        price_share = None
    else:
        character = VOLUME
        price_share = None

    if gm < gm_prior - 1.0:
        flags.append(f"gross margin down {gm_prior - gm:.1f}pp — the earliest pricing tell, "
                     f"and it moves BEFORE net margin does")

    # Inventory build into a price spike is the distributor-stuffing pattern:
    # stock accumulated at peak prices is what gets written down on the turn.
    if character == PRICE_SPIKE and inv_d and inv_dp and inv_d > inv_dp * 1.05:
        flags.append(f"inventory days RISING ({inv_dp:.0f} -> {inv_d:.0f}) during a price "
                     f"spike — stock is being built at peak prices")
    elif character == PRICE_SPIKE and inv_d and inv_dp and inv_d < inv_dp:
        flags.append(f"inventory days FALLING ({inv_dp:.0f} -> {inv_d:.0f}) — corroborates "
                     f"a genuine physical shortage rather than a paper one")

    if inv_chg > rev_g + 25 and rev_g > 0:
        flags.append(f"inventory +{inv_chg:.0f}% vs revenue +{rev_g:.0f}% — building far "
                     f"ahead of sales (a backlog build, or demand slipping)")

    return Operating(
        symbol=sym, basis=e.get("basis", "?"), filing=e.get("filing", "?"),
        rev_growth_pct=round(rev_g, 1), cogs_growth_pct=round(cogs_g, 1),
        gross_margin_pct=round(gm, 2), gross_margin_prior_pct=round(gm_prior, 2),
        inventory_days=round(inv_d, 1) if inv_d else None,
        inventory_days_prior=round(inv_dp, 1) if inv_dp else None,
        inventory_change_pct=round(inv_chg, 1),
        cost_elasticity=round(elas, 3) if elas is not None else None,
        character=character, price_share_pct=price_share, flags=flags)


def concentration(e: dict, key: str = "geography") -> list[dict]:
    """Revenue concentration by segment or geography, as a share of the total."""
    block = e.get(key) or {}
    total = e.get("revenue_current") or 0
    out = []
    for name, v in block.items():
        if not total:
            continue
        out.append({"name": name,
                    "share_pct": round(v["current"] / total * 100, 1),
                    "growth_pct": round(_pct(v["current"], v["prior"]), 1)})
    return sorted(out, key=lambda r: -r["share_pct"])


def latest_snapshot() -> tuple[dict, str] | tuple[None, None]:
    files = sorted(FUNDAMENTALS_DIR.glob("filings_*.json")) if FUNDAMENTALS_DIR.exists() else []
    if not files:
        return None, None
    return json.loads(files[-1].read_text(encoding="utf-8")), files[-1].stem.replace("filings_", "")


def report() -> dict:
    snap, snap_date = latest_snapshot()
    if snap is None:
        return {"error": "no filings snapshot in data/fundamentals/", "rows": []}
    rows = []
    for sym, e in snap["symbols"].items():
        op = analyse(sym, e)
        rows.append({"op": op,
                     "geography": concentration(e, "geography"),
                     "segments": concentration(e, "segments")})
    # Most price-driven first — that is the risk this module exists to surface.
    rows.sort(key=lambda r: (r["op"].cost_elasticity if r["op"].cost_elasticity is not None else 9))
    return {"snapshot_date": snap_date, "rows": rows, "caveat": snap.get("caveat", "")}


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rep = report()
    if "error" in rep:
        print(rep["error"])
        return
    if args.json:
        print(json.dumps({"snapshot_date": rep["snapshot_date"],
                          "rows": [{"op": r["op"].to_dict(), "geography": r["geography"],
                                    "segments": r["segments"]} for r in rep["rows"]]}, indent=2))
        return

    print("=" * 78)
    print("  OPERATING CHARACTER — from primary SEC filings (XBRL facts)")
    print("=" * 78)
    print(f"  snapshot {rep['snapshot_date']}   ·   is revenue growth PRICE or VOLUME?\n")
    print(f"  {'sym':6} {'basis':9} {'rev%':>8} {'cogs%':>8} {'elast':>7} "
          f"{'GM%':>7} {'dGM':>7} {'inv-d':>7}  character")
    print("  " + "-" * 74)
    for r in rep["rows"]:
        o = r["op"]
        if args.symbol and o.symbol != args.symbol.upper():
            continue
        el = f"{o.cost_elasticity:.2f}" if o.cost_elasticity is not None else "  -"
        iv = f"{o.inventory_days:.0f}" if o.inventory_days else "  -"
        print(f"  {o.symbol:6} {o.basis:9} {o.rev_growth_pct:>+8.1f} {o.cogs_growth_pct:>+8.1f} "
              f"{el:>7} {o.gross_margin_pct:>7.1f} {o.margin_delta_pp:>+7.1f} {iv:>7}  {o.character}")

    for r in rep["rows"]:
        o = r["op"]
        if args.symbol and o.symbol != args.symbol.upper():
            continue
        if not (o.flags or r["geography"] or r["segments"]):
            continue
        print(f"\n  {o.symbol}  ({o.filing})")
        for f in o.flags:
            print(f"    !  {f}")
        for label, block in (("segment", r["segments"]), ("region", r["geography"])):
            for c in block[:4]:
                print(f"    ·  {label} {c['name']:12} {c['share_pct']:>5.1f}% of revenue "
                      f"({c['growth_pct']:+.0f}% YoY)")
                if label == "region" and c["name"] in {"CN", "HK"} and c["share_pct"] >= 25:
                    print(f"       ^^ China exposure at {c['share_pct']:.0f}% intersects the "
                          f"china_domestic_substitution fact in app/macro/facts.py")

    print(f"\n  NOTE  {rep['caveat']}")


if __name__ == "__main__":
    _main()
