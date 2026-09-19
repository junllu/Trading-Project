"""Structural theses, decomposed into series the real world publishes.

A macro narrative — "AI displaces white-collar labour and the credit built on
those incomes breaks", "America retrenches to its own hemisphere" — is not
tradeable and not gradeable in the form it arrives in. It is a story. Stories
are confirmed by whichever headline you read last, which is how a thesis
survives for years without ever being right.

This module does one thing: it forces a narrative to name the PUBLISHED SERIES
that would move if it were true, in which direction, and from what baseline.
After that the thesis grades itself as the data lands, and neither the author
nor the operator gets a vote.

    narrative   "white-collar work is being displaced"   -> unfalsifiable
    doctrine    USINFO and USPBS payrolls FALL, JTSLDL    -> checkable monthly,
                RISES, LNS14027662 RISES, from a fixed        against a baseline
                2026-02-22 baseline                           nobody can move

WHAT THIS IS NOT

Not a signal generator. Nothing here sizes a position or emits a bias. The
existing macro layer already separates *signals* (tradeable state) from *facts*
(dated structural conditions); a doctrine is a third thing — a HYPOTHESIS UNDER
TEST — and conflating it with either is what lets a story quietly become a
position. Confirmation here is evidence about a worldview, not a reason to buy.

PRICE IS NOT AN INDICATOR HERE, ON PURPOSE

The obvious way to grade a thesis is to check whether the named tickers went
the predicted way. That is a test of TIMING, not of mechanism, and on a
multi-year structural call it is close to noise: every name Citrini named short
in Feb 2026 outperformed SPY over the following six months while the underlying
labour series had barely printed. A doctrine is graded on its mechanism or not
at all.

BASELINE, LAG, AND WHY BOTH ARE STORED

Every reading is compared against the value AS OF the doctrine's baseline date,
not against some rolling window, so "has it tracked" has exactly one answer.
Each series also carries its real publication lag: JOLTS runs ~45 days behind,
GDP ~90. A doctrine that looks unconfirmed may simply not have data yet, and
PENDING is reported separately from CONTRADICTED — collapsing them would let a
slow series read as a refutation.

    python -m app.macro.doctrine
    python -m app.macro.doctrine --key intelligence_displacement
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from ..config import ROOT

CACHE_DIR = ROOT / "data" / "macro_series"
CACHE_TTL_HOURS = 12
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}"

CONFIRMS_RISING, CONFIRMS_FALLING = "rising", "falling"
CONFIRMED, CONTRADICTED, NEUTRAL, PENDING = ("CONFIRMED", "CONTRADICTED",
                                             "NEUTRAL", "PENDING")

# Below this, a move is inside normal revision noise and is called NEUTRAL
# rather than being scored in either direction.
NOISE_BAND_PCT = 1.0


@dataclass
class Indicator:
    key: str
    series: str                 # FRED id, verified to resolve
    label: str
    confirms: str               # CONFIRMS_RISING | CONFIRMS_FALLING
    tests: str                  # which link in the mechanism this checks
    lag_days: int = 45          # real publication lag
    target: float | None = None  # an explicit level the thesis named
    target_by: str = ""
    # A derived indicator is computed as (series - series_b) / series_b * 100.
    series_b: str = ""


@dataclass
class Doctrine:
    key: str
    headline: str
    source: str
    baseline: str               # date all readings are compared against
    horizon: str                # when the thesis resolves
    mechanism: str
    falsifier: str
    indicators: list[Indicator] = field(default_factory=list)
    note: str = ""


# --- the doctrines ---------------------------------------------------------
# Series IDs below were each verified to resolve against FRED before being
# written down. An indicator naming a series that 404s is worse than no
# indicator: it reports PENDING forever and reads as "too early to say".

DOCTRINES: list[Doctrine] = [
    Doctrine(
        key="intelligence_displacement",
        headline="AI displaces white-collar labour; the credit underwritten on those incomes breaks",
        source="Citrini Research, 'The 2028 Global Intelligence Crisis', citriniresearch.com/p/2028gic",
        baseline="2026-02-22",
        horizon="2028-06-30",
        mechanism=("capability improves -> payroll shrinks -> spending softens -> "
                   "margins tighten -> more capability bought -> loop accelerates, "
                   "ending in mortgage stress in tech-heavy metros"),
        falsifier=("information and professional-services payrolls keep RISING through "
                   "2027 while layoffs and graduate unemployment stay flat — the "
                   "displacement link never appears in the labour data, so nothing "
                   "downstream of it can be caused by this mechanism"),
        note=("Graded on mechanism only. The named shorts (NOW, MA, V, APO, KKR) all "
              "beat SPY in the first six months, which says nothing about a thesis "
              "whose first checkpoint is 2026 Q3 and whose horizon is mid-2028."),
        indicators=[
            Indicator("info_payrolls", "USINFO", "Information-sector payrolls (000s)",
                      CONFIRMS_FALLING, "the displacement itself, at its most direct", 35),
            Indicator("pbs_payrolls", "USPBS", "Professional & business services payrolls (000s)",
                      CONFIRMS_FALLING, "white-collar employment broadly", 35),
            Indicator("grad_unemployment", "LNS14027662",
                      "Unemployment rate, bachelor's degree and higher, 25+",
                      CONFIRMS_RISING, "whether displacement is concentrated in the educated", 35),
            Indicator("layoffs", "JTSLDL", "JOLTS layoffs & discharges (000s)",
                      CONFIRMS_RISING, "the flow, not the stock — turns before payrolls", 60),
            Indicator("openings", "JTSJOL", "JOLTS job openings (000s)",
                      CONFIRMS_FALLING, "demand for labour ahead of separations", 60),
            Indicator("unemployment", "UNRATE", "Unemployment rate (%)",
                      CONFIRMS_RISING, "the headline the thesis names explicitly",
                      35, target=10.2, target_by="2028-06-30"),
            Indicator("real_consumption", "PCEC96", "Real personal consumption (bn, chained)",
                      CONFIRMS_FALLING, "the 'spending softens' link", 35),
            Indicator("ghost_gdp", "GDP", "GDP minus GDI, % of GDI ('ghost GDP' proxy)",
                      CONFIRMS_RISING,
                      "output booked in the accounts that never becomes income — "
                      "the closest published proxy for the coined term",
                      90, series_b="GDI"),
            Indicator("mortgage_delinquency", "DRSFRMACBS",
                      "Single-family mortgage delinquency rate (%)",
                      CONFIRMS_RISING, "the terminal claim: incomes fail, mortgages follow", 90),
            Indicator("hy_spreads", "BAMLH0A0HYM2", "US high-yield OAS (%)",
                      CONFIRMS_RISING, "the credit-seizure link, priced daily", 1),
        ],
    ),
    Doctrine(
        key="hemispheric_realignment",
        headline="US retrenches from global guarantor to hemispheric sphere — tariffs, "
                 "reshoring, and trade reoriented away from China toward near neighbours",
        source="operator frame (Pax Americana / hemispheric doctrine) — NOT a sourced "
               "publication; the baseline below is a tracking anchor, not a release date",
        baseline="2025-01-20",
        horizon="2028-12-31",
        mechanism=("tariff wall raised -> imports reroute from China to hemisphere -> "
                   "domestic manufacturing capacity is rebuilt -> defence spending "
                   "reorients to the near abroad"),
        falsifier=("imports from China recover toward trend while manufacturing "
                   "construction rolls over and customs receipts fall — the trade "
                   "reorientation reverses and the doctrine is rhetoric only"),
        note=("Deliberately measured on trade and capex flows rather than on speeches. "
              "Policy intent is not observable; cargo and concrete are."),
        indicators=[
            Indicator("imports_china", "IMPCH", "US goods imports from China ($mn)",
                      CONFIRMS_FALLING, "decoupling from the primary strategic rival", 50),
            Indicator("imports_mexico", "IMPMX", "US goods imports from Mexico ($mn)",
                      CONFIRMS_RISING, "nearshoring into the hemisphere", 50),
            Indicator("mfg_construction", "TLMFGCONS",
                      "Manufacturing construction spending ($mn, SAAR)",
                      CONFIRMS_RISING, "reshoring in concrete, not in press releases", 60),
            Indicator("customs_duties", "B235RC1Q027SBEA", "Customs duties receipts ($bn, SAAR)",
                      CONFIRMS_RISING, "whether the tariff wall is real and collected", 90),
            Indicator("defense_spend", "FDEFX", "Federal defence consumption ($bn, SAAR)",
                      CONFIRMS_RISING, "resourcing behind the posture", 90),
        ],
    ),
]

BY_KEY = {d.key: d for d in DOCTRINES}


# --- series access ---------------------------------------------------------

def _fetch(series: str) -> list[tuple[str, float]]:
    """FRED CSV, cached on disk. Returns [(date, value)] ascending."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{series}.csv"
    fresh = (cache.exists() and
             time.time() - cache.stat().st_mtime < CACHE_TTL_HOURS * 3600)
    if not fresh:
        try:
            import requests
            r = requests.get(FRED_CSV.format(sid=series), timeout=30)
            if r.status_code == 200 and "," in r.text:
                cache.write_text(r.text, encoding="utf-8")
        except Exception:
            pass                                  # fall through to stale cache
    if not cache.exists():
        return []
    out = []
    for row in csv.reader(io.StringIO(cache.read_text(encoding="utf-8"))):
        if len(row) < 2:
            continue
        try:
            out.append((row[0], float(row[1])))
        except ValueError:
            continue                              # header, or FRED's "." for missing
    return out


def _value_on_or_before(rows: list[tuple[str, float]], when: str):
    prior = [r for r in rows if r[0] <= when]
    return prior[-1] if prior else None


def _reading(ind: Indicator, baseline: str) -> dict:
    rows = _fetch(ind.series)
    if not rows:
        return {"status": PENDING, "why": f"series {ind.series} unavailable"}

    if ind.series_b:
        rows_b = _fetch(ind.series_b)
        if not rows_b:
            return {"status": PENDING, "why": f"series {ind.series_b} unavailable"}
        b_map = dict(rows_b)
        rows = [(d, (v - b_map[d]) / b_map[d] * 100.0)
                for d, v in rows if d in b_map and b_map[d]]
        if not rows:
            return {"status": PENDING, "why": "no overlapping dates for the derived series"}

    base = _value_on_or_before(rows, baseline)
    if base is None:
        return {"status": PENDING, "why": f"no observation at or before {baseline}"}
    last_date, last_val = rows[-1]

    # A series whose newest print predates the baseline cannot say anything yet.
    if last_date <= baseline:
        return {"status": PENDING, "why": f"no print since the baseline ({last_date})"}

    change = last_val - base[1]
    pct = (change / abs(base[1]) * 100.0) if base[1] else 0.0
    moved = CONFIRMS_RISING if change > 0 else CONFIRMS_FALLING

    if abs(pct) < NOISE_BAND_PCT:
        status = NEUTRAL
    elif moved == ind.confirms:
        status = CONFIRMED
    else:
        status = CONTRADICTED

    stale_days = (date.today() - datetime.strptime(last_date, "%Y-%m-%d").date()).days
    return {
        "status": status,
        "baseline_date": base[0], "baseline_value": round(base[1], 3),
        "latest_date": last_date, "latest_value": round(last_val, 3),
        "change": round(change, 3), "change_pct": round(pct, 2),
        "expected": ind.confirms, "observed": moved,
        "stale_days": stale_days,
        "overdue": stale_days > ind.lag_days + 35,
        "target": ind.target, "target_by": ind.target_by,
        "target_gap": (round(ind.target - last_val, 2)
                       if ind.target is not None else None),
    }


def track(key: str) -> dict:
    d = BY_KEY.get(key)
    if not d:
        return {"error": f"unknown doctrine '{key}'"}
    rows = []
    for ind in d.indicators:
        r = _reading(ind, d.baseline)
        rows.append({"key": ind.key, "label": ind.label, "series": ind.series,
                     "tests": ind.tests, **r})
    tally = {s: sum(1 for r in rows if r["status"] == s)
             for s in (CONFIRMED, CONTRADICTED, NEUTRAL, PENDING)}
    scored = tally[CONFIRMED] + tally[CONTRADICTED]
    return {
        "key": d.key, "headline": d.headline, "source": d.source,
        "baseline": d.baseline, "horizon": d.horizon,
        "mechanism": d.mechanism, "falsifier": d.falsifier, "note": d.note,
        "indicators": rows, "tally": tally,
        "confirmed_share": round(tally[CONFIRMED] / scored, 3) if scored else None,
        "verdict": _verdict(tally, scored),
    }


def _verdict(tally: dict, scored: int) -> str:
    if scored < 3:
        return (f"TOO EARLY — only {scored} indicator(s) have moved beyond noise; "
                f"{tally[PENDING]} still pending publication")
    share = tally[CONFIRMED] / scored
    if share >= 0.7:
        return f"TRACKING — {tally[CONFIRMED]}/{scored} moved the predicted way"
    if share <= 0.3:
        return f"NOT TRACKING — {tally[CONTRADICTED]}/{scored} moved against the thesis"
    return f"MIXED — {tally[CONFIRMED]} for, {tally[CONTRADICTED]} against"


def report() -> dict:
    return {"as_of": date.today().isoformat(),
            "doctrines": [track(d.key) for d in DOCTRINES]}


def _main() -> None:
    ap = argparse.ArgumentParser(description="Track structural theses against published series.")
    ap.add_argument("--key")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    keys = [args.key] if args.key else [d.key for d in DOCTRINES]
    if args.json:
        print(json.dumps([track(k) for k in keys], indent=2))
        return

    MARK = {CONFIRMED: "CONFIRM", CONTRADICTED: "AGAINST",
            NEUTRAL: "  flat ", PENDING: "pending"}
    for k in keys:
        t = track(k)
        if "error" in t:
            print(t["error"]); continue
        print("=" * 86)
        print(f"  {t['headline']}")
        print(f"  {t['source']}")
        print(f"  baseline {t['baseline']}  ->  horizon {t['horizon']}")
        print("=" * 86)
        print(f"\n  {t['verdict']}\n")
        print(f"  {'indicator':<46} {'baseline':>10} {'latest':>10} {'chg':>8}  verdict")
        print("  " + "-" * 82)
        for r in t["indicators"]:
            if r["status"] == PENDING:
                print(f"  {r['label'][:46]:<46} {'—':>10} {'—':>10} {'—':>8}  "
                      f"{MARK[r['status']]}  ({r.get('why','')})")
                continue
            flag = " !stale" if r.get("overdue") else ""
            print(f"  {r['label'][:46]:<46} {r['baseline_value']:>10.2f} "
                  f"{r['latest_value']:>10.2f} {r['change_pct']:>7.1f}%  "
                  f"{MARK[r['status']]}{flag}")
            if r.get("target") is not None:
                print(f"  {'':<46} thesis names {r['target']} by {r['target_by']} "
                      f"— gap {r['target_gap']:+.2f}")
        print(f"\n  FALSIFIER  {t['falsifier']}")
        if t["note"]:
            print(f"  NOTE       {t['note']}")
        print()


if __name__ == "__main__":
    _main()
