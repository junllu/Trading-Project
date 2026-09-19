"""Published macro series — the MAKER that feeds doctrine grading.

WHY THIS EXISTS AS A SEPARATE AGENT

app/macro/doctrine.py graded structural theses AND fetched the series it graded
them against. That is precisely the violation the roster is built to prevent: a
checker supplying its own evidence. The graph states it plainly — "a checker with
no maker input is grading its own opinion" — and the concrete failure it points
at is thesis_ledger passing ANET on net margin while the filing showed COGS
growing 46.9% against revenue's 37.7%. A checker reading its own preferred
number is not caught by more care; it is caught by a second, independent
supplier.

So the split: this module OWNS the pulls and the cache. doctrine.py judges what
it finds there and can no longer choose what to fetch.

WHAT IT GUARANTEES

  · Every series named by any doctrine is refreshed on one cadence.
  · A failed pull leaves the previous cache intact and is REPORTED, never
    silently swallowed — a doctrine reading stale data must be able to say so.
  · Staleness is measured against each series' real publication frequency, so a
    quarterly series is not flagged for being three weeks old.

    python -m app.macro.series
    python -m app.macro.series --refresh
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import time
from datetime import date, datetime

from .doctrine import CACHE_DIR, DOCTRINES, FRED_CSV

# How long before a series is overdue, measured from its OBSERVATION date.
#
# The subtlety that makes the naive threshold useless: FRED stamps an
# observation with the START of the period it covers, and then publishes it
# months later. Q2-2026 GDP is dated 2026-04-01 and lands at the end of July, so
# a perfectly current quarterly series is ~160 days old by observation date, and
# ~250 just before the next release. Thresholds set on the publication lag alone
# mark every series STALE, which is how a health report becomes wallpaper.
#
# These are set past the point where the NEXT release should already have
# arrived, so a flag means a genuinely missed publication.
STALE_AFTER_DAYS = {
    "daily": 5,          # a market series absent for a week is a real failure
    "monthly": 100,      # obs date + ~45d publication + a month of slack
    "quarterly": 220,    # obs date + ~120d publication + a quarter of slack
}

# Publication rhythm per series. Anything unlisted is assumed monthly, which is
# the common case and errs toward flagging rather than hiding.
RHYTHM = {
    "BAMLH0A0HYM2": "daily",
    "GDP": "quarterly", "GDI": "quarterly", "DRSFRMACBS": "quarterly",
    "FDEFX": "quarterly", "B235RC1Q027SBEA": "quarterly",
}


def series_in_use() -> list[str]:
    """Every FRED id any doctrine depends on, de-duplicated."""
    out: list[str] = []
    for d in DOCTRINES:
        for i in d.indicators:
            for s in (i.series, i.series_b):
                if s and s not in out:
                    out.append(s)
    return out


def _read_cache(sid: str) -> list[tuple[str, float]]:
    p = CACHE_DIR / f"{sid}.csv"
    if not p.exists():
        return []
    rows = []
    for row in csv.reader(io.StringIO(p.read_text(encoding="utf-8"))):
        if len(row) < 2:
            continue
        try:
            rows.append((row[0], float(row[1])))
        except ValueError:
            continue
    return rows


def refresh(sid: str) -> dict:
    """Pull one series. A failure preserves the existing cache."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    p = CACHE_DIR / f"{sid}.csv"
    try:
        import requests
        r = requests.get(FRED_CSV.format(sid=sid), timeout=30)
        if r.status_code != 200 or "," not in r.text:
            return {"series": sid, "ok": False, "error": f"HTTP {r.status_code}"}
        body = r.text
        if len([l for l in body.splitlines() if l.strip()]) < 3:
            return {"series": sid, "ok": False, "error": "empty payload"}
        p.write_text(body, encoding="utf-8")
        return {"series": sid, "ok": True}
    except Exception as exc:
        return {"series": sid, "ok": False, "error": f"{type(exc).__name__}: {exc}"}


def status() -> dict:
    """Coverage and staleness for every series a doctrine depends on."""
    rows = []
    for sid in series_in_use():
        cached = _read_cache(sid)
        if not cached:
            rows.append({"series": sid, "cached": False, "stale": True,
                         "note": "never fetched"})
            continue
        last_date = cached[-1][0]
        try:
            age = (date.today() - datetime.strptime(last_date, "%Y-%m-%d").date()).days
        except ValueError:
            age = None
        rhythm = RHYTHM.get(sid, "monthly")
        limit = STALE_AFTER_DAYS[rhythm]
        rows.append({
            "series": sid, "cached": True, "observations": len(cached),
            "last_observation": last_date, "age_days": age,
            "rhythm": rhythm, "stale": (age is not None and age > limit),
            "file_age_hours": round(
                (time.time() - (CACHE_DIR / f"{sid}.csv").stat().st_mtime) / 3600, 1),
        })
    stale = [r["series"] for r in rows if r.get("stale")]
    missing = [r["series"] for r in rows if not r.get("cached")]
    return {"series": rows, "count": len(rows), "stale": stale, "missing": missing,
            "healthy": not stale and not missing}


def refresh_all() -> dict:
    results = [refresh(s) for s in series_in_use()]
    failed = [r for r in results if not r["ok"]]
    return {"refreshed": len(results) - len(failed), "failed": failed,
            "status": status()}


def report() -> dict:
    """Roster / orchestrator entrypoint — read-only, no network."""
    s = status()
    return {"agent": "macro_series", "kind": "maker",
            "as_of": date.today().isoformat(), **s}


def _main() -> None:
    ap = argparse.ArgumentParser(description="Published macro series cache.")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.refresh:
        r = refresh_all()
        print(f"refreshed {r['refreshed']}/{len(series_in_use())}")
        for f in r["failed"]:
            print(f"  FAILED {f['series']}: {f['error']}")
        s = r["status"]
    else:
        s = status()

    if args.json:
        print(json.dumps(s, indent=2))
        return

    print(f"\n  {len(s['series'])} series feeding {len(DOCTRINES)} doctrine(s)")
    print(f"  {'series':<18} {'obs':>6} {'last':>12} {'age':>6}  rhythm")
    for r in sorted(s["series"], key=lambda x: (not x.get("stale"), x["series"])):
        if not r.get("cached"):
            print(f"  {r['series']:<18} {'—':>6} {'never fetched':>12}")
            continue
        mark = " STALE" if r["stale"] else ""
        print(f"  {r['series']:<18} {r['observations']:>6} {r['last_observation']:>12} "
              f"{r['age_days']:>5}d  {r['rhythm']}{mark}")
    if s["missing"]:
        print(f"\n  never fetched: {', '.join(s['missing'])} — run --refresh")
    if s["stale"]:
        print(f"  stale: {', '.join(s['stale'])}")


if __name__ == "__main__":
    _main()
