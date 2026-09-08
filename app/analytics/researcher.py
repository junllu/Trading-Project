"""Researcher — outward coverage briefs (maker; never grades, never trades).

Discovery finds names you do not own. Deep dive assembles what is already on
disk about a name. This agent runs both on a clock, writes a dated brief, and
stops. Judgement stays with checkers (thesis_ledger / onboarding_gate / chief).

    python -m app.analytics.researcher
    python -m app.analytics.researcher --limit 8 --json
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from ..config import ROOT

OUT_DIR = ROOT / "data" / "research"


def _sentiment() -> dict[str, Any]:
    """The three sentiment layers that survived measurement.

    Deliberately NOT scraped mention-volume. That was measured against the core
    leg and rejected: a spike's half-life is weeks and the holding period is
    years, and roster invariant 5 blocks fast-decay evidence there outright.
    What remains is narrower and checkable:

        curated sources  Serenity et al, RIPENED before they count. Her measured
                         accuracy runs 25% at 30 days to 56% to date, so a fresh
                         view is worse than useless and the blend now waits.
        theme rotation   price-derived, so it cannot be talked into a narrative.
        sector flow      1-month relative strength across the whole market —
                         where money actually moved, not where a feed says it
                         should have.
    """
    out: dict[str, Any] = {}
    try:
        from ..intel.feeds import SOURCE_RIPEN_DAYS, report as feeds_report
        f = feeds_report()
        out["curated_sources"] = {"live": f.get("live", []), "stale": f.get("stale", []),
                                  "ripen_days": SOURCE_RIPEN_DAYS}
    except Exception as exc:
        out["curated_sources"] = {"error": type(exc).__name__}
    try:
        from ..intel.themes import ALIVE, report as themes_report
        t = themes_report()
        alive = {n: v for n, v in t["themes"].items() if v["state"] == ALIVE}
        out["themes"] = {
            "alive": sorted(alive),
            # The gap that matters most: a theme working, with nothing in it.
            "alive_without_exposure": sorted(n for n, v in alive.items()
                                             if not v["exposure_usd"]),
            "exposed_to_rotating": t.get("exposed_to_rotating_themes", {}),
        }
    except Exception as exc:
        out["themes"] = {"error": type(exc).__name__}
    try:
        from .sectors import report as sectors_report
        sec = sectors_report()
        out["sector_flow"] = {k: sec.get(k) for k in
                              ("leaders_1m", "laggards_1m", "improving",
                               "defensive_leadership", "weakening_with_exposure")}
    except Exception as exc:
        out["sector_flow"] = {"error": type(exc).__name__}
    return out


def _valuation_context(symbols: list[str]) -> dict[str, Any]:
    """Peer standing for shortlisted names — the operator's primary screen."""
    try:
        from .peer_value import build
        standing = {st.symbol: {"band": st.band, "vs_median_x": st.vs_median_x,
                                "rank": f"{st.rank}/{st.of}", "pe": st.pe}
                    for g in build() for st in g.standings}
    except Exception:
        return {}
    return {s: standing[s] for s in symbols if s in standing}


def _safe_dossier(symbol: str) -> dict[str, Any]:
    try:
        from .deep_dive import dossier
        return dossier(symbol, {})
    except Exception as exc:
        return {"symbol": symbol, "error": str(exc), "available": False}


def report(limit: int = 8) -> dict[str, Any]:
    """Roster / orchestrator entrypoint — no args beyond defaults."""
    from .discovery import report as discovery_report, scan

    disc = discovery_report()
    shortlist = list(disc.get("shortlist") or [])[:limit]
    # Prefer Candidate dicts when scan works
    try:
        rows = scan()[:limit]
        candidates = [c.to_dict() if hasattr(c, "to_dict") else dict(c) for c in rows]
        shortlist = [c.get("symbol") for c in candidates if c.get("symbol")][:limit]
    except Exception:
        candidates = [{"symbol": s} for s in shortlist]

    dossiers = []
    for sym in shortlist:
        d = _safe_dossier(sym)
        # Strip huge blobs if present — keep a compact research card
        if isinstance(d, dict) and len(json.dumps(d, default=str)) > 20_000:
            d = {
                "symbol": sym,
                "held": (d.get("position") or {}).get("held"),
                "metrics": d.get("metrics"),
                "truncated": True,
            }
        dossiers.append(d)

    brief = {
        "as_of": time.strftime("%Y-%m-%d %H:%M:%S"),
        "agent": "researcher",
        "kind": "maker",
        "discovery": {
            "scanned": disc.get("scanned"),
            "in_live_themes": disc.get("in_live_themes"),
            "shortlist": shortlist,
            "needs_mcp": disc.get("needs_mcp") or [],
            "note": disc.get("note"),
        },
        "candidates": candidates,
        "valuation": _valuation_context(shortlist),
        "sentiment": _sentiment(),
        "dossiers": dossiers,
        "mcp_queue": disc.get("needs_mcp") or [],
        "does_not": [
            "assign buy/sell ratings",
            "place or stage orders",
            "rewrite strategy rules",
        ],
        "next_checkers": ["onboarding_gate", "thesis_ledger", "chief"],
    }

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    day = time.strftime("%Y%m%d")
    path = OUT_DIR / f"brief_{day}.json"
    path.write_text(json.dumps(brief, indent=2, default=str), encoding="utf-8")
    latest = OUT_DIR / "brief_latest.json"
    latest.write_text(json.dumps(brief, indent=2, default=str), encoding="utf-8")
    brief["written_to"] = str(path)
    return brief


def latest() -> dict[str, Any]:
    p = OUT_DIR / "brief_latest.json"
    if not p.exists():
        return report()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return report()


def _main() -> None:
    ap = argparse.ArgumentParser(description="Researcher maker — outward coverage brief")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    brief = report(limit=args.limit)
    if args.json:
        print(json.dumps(brief, indent=2, default=str))
        return
    print("=" * 64)
    print("RESEARCHER — outward brief (no grades, no orders)")
    print("=" * 64)
    print(f"shortlist : {', '.join(brief['discovery'].get('shortlist') or []) or '(empty)'}")
    sent = brief.get("sentiment") or {}
    th, sf = sent.get("themes") or {}, sent.get("sector_flow") or {}
    cs = sent.get("curated_sources") or {}
    print(f"themes    : alive {', '.join(th.get('alive') or []) or '—'}")
    if th.get("alive_without_exposure"):
        print(f"            ALIVE, NO EXPOSURE: {', '.join(th['alive_without_exposure'])}")
    print(f"sectors   : leading {', '.join(sf.get('leaders_1m') or []) or '—'}")
    print(f"sources   : live {', '.join(cs.get('live') or []) or 'none'}"
          f"  ·  stale {', '.join(cs.get('stale') or []) or 'none'}")
    mcp = brief.get("mcp_queue") or []
    if mcp:
        print("MCP queue (human):")
        for m in mcp[:8]:
            print(f"  · {m}")
    print(f"wrote     : {brief.get('written_to')}")
    print("Checkers next: onboarding_gate → thesis_ledger → chief")


if __name__ == "__main__":
    _main()
