"""Thesis ledger — why we hold each core name, and what would prove us wrong.

The core pool is bought to be held to the anticipated 2028 semiconductor peak.
Over that horizon price signals are noise: a name can be down 38% and more right
than when it was up 150%. What actually decides the outcome is whether the
*claim* is still true, so that is what this tracks.

Every thesis carries checkpoints written BEFORE the outcome is known, each one a
threshold on a number we can pull. That ordering is the whole point. A thesis you
can restate after the fact to fit whatever happened has told you nothing; one that
says "revenue YoY below 25% falsifies this" either survives the next print or
does not. It is the same discipline as app/analytics/confidence.py applies to
signals, moved up to the level of the holding.

Two things this deliberately does NOT do:

  It does not read price. Drawdown is reported for context and never grades a
  checkpoint — otherwise the ledger drifts into momentum-following wearing a
  research costume.

  It does not call a network. It grades against a dated snapshot in
  data/fundamentals/, pulled through the broker MCP by hand. The refresh is a
  timestamped act, so an old grade is visibly old rather than silently stale.

The cyclical trap this exists to catch: at a cycle top a memory maker prints
record margins, which collapses its P/E, which reads as "cheap" on every screen
ever written. The low multiple IS the warning. `peak_margin_watch` below fires on
exactly that shape — record trailing margin plus a low P/E in a cyclical sector —
because the campaign's exit thesis depends on not mistaking it for a bargain.

    python -m app.intel.thesis
    python -m app.intel.thesis --symbol MU
    python -m app.intel.thesis --broken-only
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import date

from ..config import ROOT

FUNDAMENTALS_DIR = ROOT / "data" / "fundamentals"
STALE_AFTER_DAYS = 45          # roughly one earnings cycle

CYCLICAL_SECTORS = {"memory", "semiconductors"}

# Verdicts, worst-first for sorting.
BROKEN, WATCH, INTACT, UNGRADED = "BROKEN", "WATCH", "INTACT", "UNGRADED"
_ORDER = {BROKEN: 0, WATCH: 1, UNGRADED: 2, INTACT: 3}


@dataclass
class Checkpoint:
    """One falsifiable test. `why` must say what breaking it would MEAN."""
    metric: str
    op: str                      # ">=" | "<="
    threshold: float
    why: str
    warn_at: float | None = None   # between warn_at and threshold -> WATCH

    def grade(self, value: float | None) -> tuple[str, str]:
        if value is None:
            return UNGRADED, f"{self.metric}: not available in the snapshot"
        ok = value >= self.threshold if self.op == ">=" else value <= self.threshold
        detail = f"{self.metric} {value:+.2f} vs {self.op} {self.threshold:+.2f}"
        if ok:
            return INTACT, detail
        if self.warn_at is not None:
            near = value >= self.warn_at if self.op == ">=" else value <= self.warn_at
            if near:
                return WATCH, detail + " (inside the warning band)"
        return BROKEN, detail


@dataclass
class Thesis:
    symbol: str
    claim: str                   # one sentence, falsifiable
    layer: str                   # Jensen's 5-layer cake position
    exit_by: str                 # the dated exit this hold is underwritten to
    exit_trigger: str            # what ends it EARLY
    checkpoints: list[Checkpoint] = field(default_factory=list)
    held: bool = True
    note: str = ""


# --- the ledger ------------------------------------------------------------
# Thresholds are set from the 2026-09-07 snapshot's own history: each one sits
# below the range the name has actually sustained, so it fires on a real break
# rather than on ordinary quarter-to-quarter wobble. Where a number was chosen
# rather than derived, the `why` says so.

LEDGER: list[Thesis] = [
    Thesis(
        symbol="ANET", layer="networking (layer 3)",
        claim=("Ethernet wins the AI back-end interconnect, and Arista holds share "
               "at hyperscalers without giving up price."),
        exit_by="2028 semiconductor peak",
        exit_trigger="net margin below 30% for two consecutive quarters — that is pricing power going, which is the entire claim",
        checkpoints=[
            Checkpoint("net_margin_ttm", ">=", 33.0, warn_at=30.0,
                       why="Held 37-41% across all 8 quarters with no cyclical dip. Below 33% means competition finally reached pricing."),
            Checkpoint("rev_yoy_pct", ">=", 20.0, warn_at=12.0,
                       why="Ran +37% YoY. Below 20% says hyperscaler build-out is decelerating ahead of schedule."),
        ],
        note="The only name in the book with NO margin cycle in 8 quarters. Highest thesis quality here.",
    ),
    Thesis(
        symbol="MRVL", layer="custom silicon (layer 2)",
        claim=("Custom XPU/ASIC programs at hyperscalers scale through 2027, and "
               "Marvell converts design wins into revenue."),
        exit_by="2028 semiconductor peak",
        exit_trigger="two consecutive quarters of sequential revenue decline — custom programs are contracted, so a decline means a program was lost",
        checkpoints=[
            Checkpoint("rev_yoy_pct", ">=", 25.0, warn_at=15.0,
                       why="+36.5% YoY latest. Custom-silicon ramps are contracted; sub-25% means a socket slipped."),
            Checkpoint("rev_qoq_pct", ">=", 0.0, warn_at=-5.0,
                       why="Sequential growth is the honest read here because net margin is corrupted by one-time items."),
        ],
        note=("Margin unusable as a signal: 91.65% (Q3 FY26) and -44.61% (Q3 FY25) are one-time "
              "items. Graded on revenue only — a deliberate choice, not an oversight. "
              "Largest single loss in the book at -$3,057; the thesis is intact while the position is not."),
    ),
    Thesis(
        symbol="ALAB", layer="connectivity (layer 3)",
        claim=("PCIe/CXL retimers are a required component of every rack-scale AI "
               "deployment, and Astera holds the design-in position."),
        exit_by="2028 semiconductor peak",
        exit_trigger="revenue growth below 40% YoY — at 150x earnings, deceleration alone re-rates the multiple violently",
        checkpoints=[
            Checkpoint("rev_yoy_pct", ">=", 60.0, warn_at=40.0,
                       why="+104% YoY latest; 3.5x revenue in 8 quarters. At a 152 P/E the growth IS the valuation."),
            Checkpoint("net_margin_ttm", ">=", 22.0, warn_at=18.0,
                       why="Averaged ~30% over 4 quarters. Retimers commoditize; margin is the early tell."),
        ],
        note="Peaked at $483.02 (+46% over the $330.50 cost) and was not sold. Currently ~-6%.",
    ),
    Thesis(
        symbol="VRT", layer="power & cooling (layer 1)",
        claim=("Data-center thermal and power density outruns the installed base, "
               "and Vertiv's margin expands as liquid cooling mixes up."),
        exit_by="2028 semiconductor peak",
        exit_trigger="net margin rolling back under 12% — the whole bull case is mix-driven margin expansion, not volume",
        checkpoints=[
            Checkpoint("net_margin_ttm", ">=", 13.0, warn_at=11.0,
                       why="Expanded 6.26% -> 15.20% over 8 quarters. That expansion is the thesis; stalling ends it."),
            Checkpoint("rev_yoy_pct", ">=", 15.0, warn_at=8.0,
                       why="+24% YoY latest."),
        ],
    ),
    Thesis(
        symbol="AEIS", layer="power conversion (layer 1)",
        claim=("Precision power supply for semi-cap and data centers is a "
               "structurally short-supplied niche Advanced Energy leads."),
        exit_by="2028 semiconductor peak",
        exit_trigger="net margin failing to hold 10% while revenue grows — that is volume bought with price",
        checkpoints=[
            Checkpoint("net_margin_ttm", ">=", 9.5, warn_at=8.0,
                       why="TTM ~10.8%, but latest quarter FELL to 9.42% from 13.07% while revenue rose. Watch this one."),
            Checkpoint("rev_yoy_pct", ">=", 20.0, warn_at=12.0,
                       why="+30% YoY latest."),
        ],
        note=("WEAKEST of the AI-infra basket. Margin went DOWN quarter-on-quarter (13.07 -> 9.42) "
              "on rising revenue — the signature of winning volume on price. Smallest market cap "
              "($11.25B) so least able to defend a niche if a larger supplier wants it."),
    ),
    Thesis(
        symbol="BE", layer="on-site generation (layer 1)",
        claim=("Grid interconnect queues make behind-the-meter fuel cells the only "
               "way to power new AI capacity on a 2-3 year timeline."),
        exit_by="2028 semiconductor peak",
        exit_trigger="any return to negative net income — at 344x earnings and 46x book there is no valuation floor to land on",
        checkpoints=[
            Checkpoint("net_margin_ttm", ">=", 8.0, warn_at=4.0,
                       why="Only just turned profitable: -10.52% -> +18.67% over 5 quarters. Profitability is one quarter old."),
            Checkpoint("rev_yoy_pct", ">=", 50.0, warn_at=25.0,
                       why="+166% YoY. At a 344 P/E only hypergrowth justifies the multiple."),
        ],
        note=("HIGHEST-RISK core hold. P/E 344, P/B 46 — the most expensive name in the book on "
              "both. Revenue is lumpy and project-timed (401 -> 519 -> 868 -> 751 -> 1065), so a "
              "single slipped project prints as a collapse. Position is $19,644 across two brokers."),
    ),
    Thesis(
        symbol="NOW", layer="application/agent layer (layer 5)",
        claim=("Enterprise AI agents need a workflow system of record, ServiceNow "
               "is the incumbent that captures it, AND it can serve those agents "
               "without giving the economics back in compute cost."),
        exit_by="NOT the 2028 semiconductor peak — this runs on its own calendar. "
                "Re-underwrite each quarter on gross margin.",
        exit_trigger=("a fourth consecutive quarter of gross-margin decline. The demand "
                      "half of the claim is already proven by $29B of RPO; only the "
                      "unit-economics half is in question, so that is what to watch"),
        checkpoints=[
            Checkpoint("net_margin_ttm", ">=", 11.0, warn_at=9.0,
                       why=("TTM 11.47%, down from 13.83% a year ago. Net margin has HALVED "
                            "at the quarterly level: 15.45% -> 7.47% across 8 quarters, with "
                            "revenue up every single quarter. Nothing one-time explains it.")),
            Checkpoint("rev_yoy_pct", ">=", 18.0, warn_at=14.0,
                       why=("+24.0% YoY. Demand is NOT the problem and this checkpoint exists "
                            "to prove that, so a break here would mean something new broke.")),
        ],
        note=("THE RECOVERY QUESTION, made testable. Getting back to the $194.73 high needs "
              "+37.9% from $141.24. At an unchanged 88x multiple that requires earnings +37.9% "
              "— while net income is currently -22.6% YoY ($385M -> $298M). That is a ~60-point "
              "swing in earnings growth, or a re-rating from 88x to ~121x on falling earnings. "
              "WHAT WOULD MAKE IT HAPPEN is specific and plausible: subscription cost of revenue "
              "grew +64.8% against +24% revenue (cost elasticity 2.56, the worst in the book) "
              "because serving AI features costs inference compute. Cheaper inference in "
              "2027-28 — better models, custom silicon, falling GPU cost per token — reverses "
              "exactly this line. So the recovery thesis is REAL but CONDITIONAL, and the "
              "condition is one number: gross margin, 79.12% -> 70.68% over 8 quarters and "
              "falling monotonically. The market did not miss this; it de-rated 27% while "
              "revenue grew 24%. Position is $42,513 — 31.7% of the book."),
    ),
    Thesis(
        symbol="ARM", layer="instruction set / IP (layer 2)",
        claim=("Every AI datacenter CPU converges on Arm, and royalty rates rise "
               "with each architecture generation."),
        exit_by="2028 semiconductor peak",
        exit_trigger="royalty rate per chip flattening — volume growth alone does not justify 258x",
        checkpoints=[],
        note="UNGRADED — no quarterly financials pulled. P/E 258. Position is 1 share; +127%.",
    ),
    Thesis(
        symbol="MU", held=False, layer="memory (layer 1 — the bottleneck)",
        claim=("2027 DRAM/HBM capacity is contracted out, so pricing holds through "
               "the year regardless of demand softness."),
        exit_by="2027 year-end, AHEAD of the 2028 capacity relief",
        exit_trigger="peak margin — see peak_margin_watch. Memory tops on margin, not on price or multiple",
        checkpoints=[
            Checkpoint("net_margin_ttm", ">=", 40.0, warn_at=30.0,
                       why="TTM ~48%. This is set to catch the ROLL, not to be reassured by the level."),
            Checkpoint("rev_yoy_pct", ">=", 100.0, warn_at=40.0,
                       why="+346% YoY. Any print near this threshold means the cycle has turned."),
        ],
        note=("NOT HELD. Read the P/E of 23 correctly: it is 23x PEAK earnings. Net margin went "
              "11.45% -> 68.13% in 8 quarters, which is not a sustainable state for a memory "
              "maker — it is what the top of a memory cycle looks like. Serenity's '3x forward "
              "P/E' point is about Samsung/SK Hynix, which are down 38% from peak; MU is not. "
              "Same thesis, opposite entry point."),
    ),
    Thesis(
        symbol="SNDK", held=False, layer="memory/NAND (layer 1)",
        claim="NAND supply stays tight into 2027 as capacity shifts toward HBM.",
        exit_by="2027 year-end",
        exit_trigger="peak margin — same cyclical logic as MU",
        checkpoints=[
            Checkpoint("net_margin_ttm", ">=", 35.0, warn_at=25.0,
                       why="TTM ~42%. Again set to catch the roll."),
            Checkpoint("rev_yoy_pct", ">=", 100.0, warn_at=40.0,
                       why="+372% YoY, off a loss-making base quarter."),
        ],
        note=("NOT HELD. More extreme than MU: 77% net margin, from -1.21% five quarters ago. "
              "The stock is up ~25x off its 52-week low of $63.74. A 23.6 P/E here is the "
              "cyclical-top signature in its purest form. Also note the nuance from the "
              "2026-09-07 source post: NAND price DECLINES can coexist with broad shortage, "
              "because Chinese module makers liquidate inventory into high prices."),
    ),
]


# --- grading ---------------------------------------------------------------

def latest_snapshot() -> tuple[dict, str] | tuple[None, None]:
    if not FUNDAMENTALS_DIR.exists():
        return None, None
    # Only the bare dated files — data/fundamentals/ also holds filings_*.json,
    # which app/intel/filings.py owns and which is not shaped like this snapshot.
    files = sorted(p for p in FUNDAMENTALS_DIR.glob("????-??-??.json"))
    if not files:
        return None, None
    return json.loads(files[-1].read_text(encoding="utf-8")), files[-1].stem


def _clean_margins(entry: dict) -> list[float]:
    """Drop quarters the snapshot itself flags as one-time items."""
    flagged = " ".join(entry.get("margin_outliers", []))
    return [q["net_margin"] for q in entry["quarters"] if q["end"] not in flagged]


def metrics(entry: dict) -> dict:
    """Derive everything the checkpoints test. Missing inputs stay None."""
    qs = entry.get("quarters", [])
    out: dict = {"pe": entry.get("pe_ratio"), "pb": entry.get("pb_ratio"),
                 "quarters": len(qs)}
    if not qs:
        return out

    clean = _clean_margins(entry)
    out["net_margin_ttm"] = round(sum(clean[:4]) / len(clean[:4]), 2) if clean else None
    out["net_margin_latest"] = qs[0]["net_margin"]

    if len(qs) >= 2 and qs[1]["revenue_m"]:
        out["rev_qoq_pct"] = round((qs[0]["revenue_m"] / qs[1]["revenue_m"] - 1) * 100, 2)
    if len(qs) >= 5 and qs[4]["revenue_m"]:
        out["rev_yoy_pct"] = round((qs[0]["revenue_m"] / qs[4]["revenue_m"] - 1) * 100, 2)

    # Margin-peak position: 0 means the most recent quarter set the record.
    if clean:
        peak = max(clean)
        out["quarters_since_margin_peak"] = clean.index(peak)
        out["margin_peak_pct"] = peak

    # The cyclical trap. Record margin + a low multiple in a cyclical sector is
    # the top, not a bargain — the multiple is low BECAUSE earnings are peaking.
    out["peak_margin_watch"] = bool(
        entry.get("sector") in CYCLICAL_SECTORS
        and out.get("quarters_since_margin_peak") == 0
        and (out.get("pe") or 999) < 30
        and (out.get("net_margin_ttm") or 0) > 30
    )
    return out


def grade(thesis: Thesis, entry: dict | None) -> dict:
    if entry is None:
        return {"symbol": thesis.symbol, "verdict": UNGRADED, "metrics": {},
                "results": [], "reason": "no entry in the snapshot"}
    m = metrics(entry)
    results = []
    for cp in thesis.checkpoints:
        v, detail = cp.grade(m.get(cp.metric))
        results.append({"metric": cp.metric, "verdict": v, "detail": detail, "why": cp.why})

    if not results:
        verdict = UNGRADED
    elif any(r["verdict"] == BROKEN for r in results):
        verdict = BROKEN
    elif any(r["verdict"] == WATCH for r in results):
        verdict = WATCH
    elif all(r["verdict"] == INTACT for r in results):
        verdict = INTACT
    else:
        verdict = UNGRADED

    # A cyclical topping out is a WATCH even with every checkpoint passing —
    # that is precisely the state where the checkpoints look best.
    if m.get("peak_margin_watch") and verdict == INTACT:
        verdict = WATCH
    return {"symbol": thesis.symbol, "verdict": verdict, "metrics": m, "results": results}


def _operating_overlay(row: dict) -> dict:
    """Maker-checker: app/intel/filings.py is the maker, this is the checker.

    Net margin is a lagging, nettable number — buybacks, tax and one-time items
    all sit between the operating business and the line these checkpoints read.
    Gross-margin erosion shows up a quarter or more earlier. ANET is exactly why
    this exists: every net-margin checkpoint passed while the filing showed COGS
    growing 46.9% against revenue's 37.7%. The checkpoint was not wrong, it was
    reading the wrong line, and only a second maker catches that.
    """
    try:
        from .filings import report as filings_report
        chars = {r["op"].symbol: r["op"] for r in filings_report().get("rows", [])}
    except Exception:
        return row
    op = chars.get(row["symbol"])
    if op is None:
        return row
    row["operating"] = {"character": op.character, "cost_elasticity": op.cost_elasticity,
                        "gross_margin_delta_pp": round(op.margin_delta_pp, 2),
                        "flags": op.flags}
    if op.character == "MARGIN_PRESSURE" and row["verdict"] == INTACT:
        row["verdict"] = WATCH
        row["results"].append({
            "metric": "gross_margin_delta_pp", "verdict": WATCH,
            "detail": f"gross margin {op.margin_delta_pp:+.1f}pp, COGS +{op.cogs_growth_pct:.1f}% "
                      f"vs revenue +{op.rev_growth_pct:.1f}%",
            "why": "FILING OVERRIDE — costs are outrunning revenue at the gross line while "
                   "net margin still looks fine. Net margin is the lagging read."})
    return row


def report() -> dict:
    snap, snap_date = latest_snapshot()
    if snap is None:
        return {"error": "no snapshot in data/fundamentals/", "theses": []}
    syms = snap.get("symbols", {})
    rows = [_operating_overlay(dict(grade(t, syms.get(t.symbol)), thesis=t)) for t in LEDGER]
    rows.sort(key=lambda r: (_ORDER[r["verdict"]], not r["thesis"].held))
    age = (date.today() - date.fromisoformat(snap_date)).days
    return {"snapshot_date": snap_date, "age_days": age,
            "stale": age > STALE_AFTER_DAYS, "theses": rows}


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=None)
    ap.add_argument("--broken-only", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rep = report()
    if "error" in rep:
        print(rep["error"])
        return
    if args.json:
        print(json.dumps({k: v for k, v in rep.items() if k != "theses"} |
                         {"theses": [{k: v for k, v in r.items() if k != "thesis"}
                                     for r in rep["theses"]]}, indent=2))
        return

    print("=" * 74)
    print("  THESIS LEDGER — core pool, underwritten to the 2028 peak")
    print("=" * 74)
    print(f"  snapshot {rep['snapshot_date']}  ({rep['age_days']}d old"
          f"{' — STALE, refresh before deciding' if rep['stale'] else ''})")

    for r in rep["theses"]:
        t: Thesis = r["thesis"]
        if args.symbol and t.symbol != args.symbol.upper():
            continue
        if args.broken_only and r["verdict"] not in (BROKEN, WATCH):
            continue
        m = r["metrics"]
        tag = "" if t.held else "  [not held]"
        print(f"\n{'-' * 74}")
        print(f"  {t.symbol:6} {r['verdict']:9} {t.layer}{tag}")
        print(f"{'-' * 74}")
        print(f"  CLAIM     {t.claim}")
        print(f"  EXIT      {t.exit_by}")
        print(f"  BREAKS ON {t.exit_trigger}")

        if m.get("quarters"):
            since = m.get("quarters_since_margin_peak")
            print(f"\n  margin TTM {m.get('net_margin_ttm')}%  "
                  f"(latest {m.get('net_margin_latest')}%, peak {m.get('margin_peak_pct')}% "
                  f"{'THIS QUARTER' if since == 0 else f'{since}q ago'})")
            print(f"  revenue    YoY {m.get('rev_yoy_pct')}%   QoQ {m.get('rev_qoq_pct')}%"
                  f"   ·  P/E {m.get('pe')}  P/B {m.get('pb')}")
        op = r.get("operating")
        if op:
            el = f"{op['cost_elasticity']:.2f}" if op["cost_elasticity"] is not None else "n/a"
            print(f"  operating  {op['character']}  ·  cost elasticity {el}  ·  "
                  f"gross margin {op['gross_margin_delta_pp']:+.1f}pp")
            for f in op["flags"]:
                print(f"     !  {f}")

        if m.get("peak_margin_watch"):
            print("\n  >> PEAK-MARGIN WATCH: record margin AND a sub-30 P/E in a cyclical")
            print("     sector. The low multiple is a symptom of peak earnings, not value.")

        for res in r["results"]:
            mark = {INTACT: "ok  ", WATCH: "WATCH", BROKEN: "BROKEN", UNGRADED: "  ? "}[res["verdict"]]
            print(f"\n  [{mark}] {res['detail']}")
            print(f"          {res['why']}")
        if not r["results"]:
            print("\n  [  ? ] no checkpoints defined — this thesis is an assertion, not a test")
        if t.note:
            print(f"\n  NOTE  {t.note}")

    print(f"\n{'=' * 74}")
    counts: dict[str, int] = {}
    for r in rep["theses"]:
        counts[r["verdict"]] = counts.get(r["verdict"], 0) + 1
    print("  " + "   ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda x: _ORDER[x[0]])))


if __name__ == "__main__":
    _main()
