"""Per-symbol dossier — every layer of analysis for one ticker, in one place.

The research this project produces currently lives in scattered CLI output:
screens in one command, macro facts in another, curated-source views in a third,
cycle stats in a saved JSON. That makes it hard to answer the only question that
matters at decision time — "what do we actually know about THIS name?"

This assembles all of it. Deliberately fast paths first (local files) with the
slow network calls optional, so the dashboard stays responsive:

    position    config/holdings.yaml          — merged across brokers
    conviction  the live portal blend         — with per-source contributions
    macro       app/macro/facts.py            — dated, sourced, point-in-time
    sources     app/intel/feeds.py            — Serenity et al., age-decayed
    cycle       data/cycle_raw.json           — median return by cycle year
    metrics     data/prices/*.csv             — vol, trend persistence, returns

Every section reports its own absence honestly rather than defaulting to zero:
"no structural fact covers this name" is a finding, not a null.

    python -m app.analytics.deep_dive MRVL
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
from datetime import date

from ..config import ROOT

CYCLE_PATH = ROOT / "data" / "cycle_raw.json"
PRICES_DIR = ROOT / "data" / "prices"

CYCLE_LABELS = {1: "Yr1 post-election", 2: "Yr2 midterm",
                3: "Yr3 pre-election", 4: "Yr4 election"}


def _cycle_year(y: int) -> int:
    return ((y - 2025) % 4) + 1


def _closes(symbol: str) -> list[float]:
    p = PRICES_DIR / f"{symbol.upper()}.csv"
    if not p.exists():
        return []
    import csv
    out = []
    with p.open("r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                out.append(float(row["close"]))
            except (KeyError, ValueError):
                continue
    return out


def _metrics(closes: list[float]) -> dict:
    if len(closes) < 60:
        return {"available": False}
    rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes)) if closes[i - 1]]
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    above = sum(1 for i in range(50, len(closes))
                if closes[i] > sum(closes[i - 50:i]) / 50)
    counted = max(1, len(closes) - 50)
    return {
        "available": True,
        "last": round(closes[-1], 2),
        "vol_annualized": round(math.sqrt(var) * math.sqrt(252), 3),
        "trend_persistence": round(above / counted, 3),
        "ret_3m_pct": round((closes[-1] / closes[-64] - 1) * 100, 1) if len(closes) > 64 else None,
        "ret_12m_pct": round((closes[-1] / closes[-253] - 1) * 100, 1) if len(closes) > 253 else None,
        "bars": len(closes),
    }


def _position(symbol: str) -> dict:
    from ..portfolio.holdings import load_holdings
    sym = symbol.upper()
    rows = [h for h in load_holdings() if str(h["symbol"]).upper() == sym]
    if not rows:
        return {"held": False}
    shares = sum(float(r["shares"]) for r in rows)
    cost = sum(float(r["shares"]) * float(r.get("avg_price", 0)) for r in rows)
    last = float(rows[-1].get("last", 0)) or 0.0
    value = shares * last
    return {
        "held": True, "shares": round(shares, 4),
        "avg_price": round(cost / shares, 2) if shares else 0.0,
        "last": last, "value": round(value, 2),
        "unrealized": round(value - cost, 2),
        "unrealized_pct": round((value / cost - 1) * 100, 2) if cost else 0.0,
        "brokers": sorted({str(r.get("broker", "?")) for r in rows}),
        "lots": [{"broker": r.get("broker"), "shares": float(r["shares"])} for r in rows],
        "covered_call_lots": int(max((float(r["shares"]) // 100) for r in rows)),
    }


def _cycle(symbol: str) -> dict:
    if not CYCLE_PATH.exists():
        return {"available": False, "reason": "run the cycle analysis first"}
    raw = json.loads(CYCLE_PATH.read_text(encoding="utf-8"))
    e = raw.get(symbol.upper())
    if not e:
        return {"available": False, "reason": "not in the cycle dataset"}
    if e["n_years"] < 12:
        return {"available": False,
                "reason": f"only {e['n_years']}y of history — under 3 cycles, not meaningful"}
    buckets: dict[int, list] = {1: [], 2: [], 3: [], 4: []}
    for y, r in e["by_year"].items():
        buckets[_cycle_year(int(y))].append(r)
    now = _cycle_year(date.today().year)
    return {
        "available": True, "n_years": e["n_years"], "current_cycle_year": now,
        "by_year": [{"year": k, "label": CYCLE_LABELS[k],
                     "median": round(st.median(v), 1), "n": len(v),
                     "is_now": k == now}
                    for k, v in buckets.items() if v],
    }


def _macro(symbol: str) -> dict:
    from ..macro.facts import facts_as_of, symbol_bias
    sym = symbol.upper()
    hits = [f for f in facts_as_of()
            if sym in {s.upper() for s in f.affected_symbols}]
    return {
        "bias": round(symbol_bias(sym), 3),
        "covered": bool(hits),
        "facts": [{"headline": f.headline, "category": f.category,
                   "direction": f.direction, "confidence": f.confidence,
                   "known_from": f.known_from, "expires": f.expires,
                   "source": f.source, "note": f.note} for f in hits],
    }


def _sources(symbol: str) -> dict:
    from ..intel.feeds import KNOWN_SOURCES, FeedStore
    store = FeedStore()
    out = []
    for src in KNOWN_SOURCES:
        for v in store.views(source=src, symbol=symbol):
            out.append({"source": v.source, "published": v.published, "bias": v.bias,
                        "confidence": v.confidence, "rationale": v.rationale})
    biases = {src: store.bias(src, symbol) for src in KNOWN_SOURCES}
    return {"views": sorted(out, key=lambda x: x["published"], reverse=True),
            "bias": {k: (round(v, 3) if v is not None else None) for k, v in biases.items()}}


def _screens(symbol: str) -> dict:
    """Screen verdicts from cached closes — no network. Volume-based checks skipped."""
    from ..analytics.screener import ScreenCriteria
    m = _metrics(_closes(symbol))
    if not m.get("available"):
        return {"available": False, "reason": "no cached price history"}
    out = {}
    for name, crit in [("core", ScreenCriteria.core()), ("options", ScreenCriteria.options()),
                       ("tactical", ScreenCriteria.tactical())]:
        reasons = []
        if m["last"] < crit.min_price:
            reasons.append(f"price ${m['last']:.2f} < ${crit.min_price:.0f}")
        if m["vol_annualized"] < crit.min_vol:
            reasons.append(f"too quiet (vol {m['vol_annualized']:.2f})")
        if m["vol_annualized"] > crit.max_vol:
            reasons.append(f"too volatile (vol {m['vol_annualized']:.2f})")
        if m["trend_persistence"] < crit.min_trend_persistence:
            reasons.append(f"no sustained trend ({m['trend_persistence']:.0%})")
        if m["trend_persistence"] > crit.max_trend_persistence:
            reasons.append(f"never pulls back ({m['trend_persistence']:.0%})")
        out[name] = {"passed": not reasons, "reasons": reasons}
    out["available"] = True
    out["note"] = "liquidity check omitted (needs volume data); run the screener CLI for full"
    return out


def dossier(symbol: str, conviction: dict | None = None) -> dict:
    sym = symbol.upper()
    return {
        "symbol": sym,
        "position": _position(sym),
        "metrics": _metrics(_closes(sym)),
        "screens": _screens(sym),
        "macro": _macro(sym),
        "sources": _sources(sym),
        "cycle": _cycle(sym),
        "conviction": conviction or {},
    }


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbol")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    d = dossier(args.symbol)
    if args.json:
        print(json.dumps(d, indent=2))
        return

    print("=" * 66)
    print(f"  {d['symbol']}")
    print("=" * 66)
    p = d["position"]
    if p["held"]:
        print(f"\nPOSITION  {p['shares']:g} sh @ ${p['avg_price']:.2f} -> ${p['last']:.2f}")
        print(f"          ${p['value']:,.0f}  ({p['unrealized_pct']:+.1f}%, "
              f"${p['unrealized']:+,.0f})  [{'+'.join(p['brokers'])}]")
        print(f"          covered-call lots available: {p['covered_call_lots']}")
    else:
        print("\nPOSITION  not held")

    m = d["metrics"]
    if m.get("available"):
        print(f"\nMETRICS   vol {m['vol_annualized']:.2f} · trend {m['trend_persistence']:.0%} "
              f"above 50d · 3m {m['ret_3m_pct']}% · 12m {m['ret_12m_pct']}%")
    s = d["screens"]
    if s.get("available"):
        print("\nSCREENS")
        for k in ("core", "options", "tactical"):
            v = s[k]
            print(f"  {k:9} {'PASS' if v['passed'] else '; '.join(v['reasons'])}")

    mc = d["macro"]
    print(f"\nMACRO     structural bias {mc['bias']:+.3f}")
    if not mc["covered"]:
        print("          no structural fact covers this name")
    for f in mc["facts"]:
        print(f"  [{f['category']}] {f['headline']}")
        print(f"     dir {f['direction']:+.2f} conf {f['confidence']:.1f} · "
              f"known {f['known_from']} -> {f['expires']} · {f['source']}")

    sc = d["sources"]
    live = {k: v for k, v in sc["bias"].items() if v is not None}
    print(f"\nSOURCES   {live if live else 'no curated view on this name'}")
    for v in sc["views"][:4]:
        print(f"  {v['published']}  {v['source']:16} {v['bias']:+.2f}  {v['rationale'][:52]}")

    c = d["cycle"]
    print("\nCYCLE", end="  ")
    if not c.get("available"):
        print(f"    {c['reason']}")
    else:
        print(f"    ({c['n_years']}y history)")
        for b in c["by_year"]:
            mark = "  <- now" if b["is_now"] else ""
            print(f"  {b['label']:20} {b['median']:>7.1f}%  (n={b['n']}){mark}")


if __name__ == "__main__":
    _main()
