"""Capital gate per strategy track — forward paper evidence decides live money.

    python -m app.analytics.track_gate

WHY THIS REPLACED THE OLD INPUT

sleeve.py graded data/forward_record.jsonl — the retired conviction blend's
signals. The strategies that could actually be funded now live in their own
paper ledgers (rev1, trend1, mom1, wheel1), so each is graded on its own
record, separately: one track's evidence never promotes another.

THE BAR (the user's, 2026-09-27: "mistakes are fine, the average must be
positive") — measured, not assumed:

  COLLECTING  not enough independent evidence yet
  $1,000      minimum evidence met, average return > 0, one-sided t >= 1.65
  $5,000      twice the evidence, t >= 2.0, max drawdown <= 25%

Evidence units: rev1 tracks = closed trades, averaged per exit DAY (same-day
trades share one market move); monthly tracks and the wheel = daily equity
returns. The SPY comparison is always reported, never required.
"""
from __future__ import annotations

import json
import math
import statistics as st

from ..config import ROOT

PAPER = ROOT / "data" / "paper"
OUT = ROOT / "data" / "track_gate.json"

MIN = {"rev1.S2": {"days": 20, "trades": 100}, "rev1.S1": {"days": 20, "trades": 60},
       "trend1": {"days": 40}, "mom1": {"days": 40}, "wheel1": {"days": 40}}
T_1K, T_5K, DD_5K = 1.65, 2.0, 25.0


def _jsonl(p) -> list[dict]:
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text("utf-8").splitlines() if x.strip()]


def _t(xs: list[float]) -> float | None:
    if len(xs) < 3 or st.stdev(xs) == 0:
        return None
    return st.mean(xs) / (st.stdev(xs) / math.sqrt(len(xs)))


def _max_dd(equity: list[float]) -> float:
    peak, dd = equity[0], 0.0
    for v in equity:
        peak = max(peak, v)
        dd = max(dd, 1 - v / peak)
    return 100 * dd


def _grade(name: str, days: int, t: float | None, mean: float | None, dd: float | None,
           extra_ok: bool = True) -> tuple[str, int, list[str]]:
    need = MIN[name]
    missing = []
    if days < need["days"]:
        missing.append(f"{days}/{need['days']} days")
    if not extra_ok:
        missing.append(f"trades below {need.get('trades')}")
    if mean is None or mean <= 0:
        missing.append("average not yet positive")
    if t is None or t < T_1K:
        missing.append(f"t {t if t is None else round(t, 2)} < {T_1K}")
    if missing:
        return "COLLECTING", 0, missing
    if days >= 2 * need["days"] and t >= T_5K and dd is not None and dd <= DD_5K:
        return "FUND $5k", 5000, []
    return "FUND $1k", 1000, [f"$5k needs {2 * need['days']} days, t >= {T_5K}, DD <= {DD_5K}%"]


def rev1_track(track: str, control: str) -> dict:
    trades = [t for t in _jsonl(PAPER / "rev1" / "trades.jsonl") if t["track"] == track]
    ctl = [t for t in _jsonl(PAPER / "rev1" / "trades.jsonl") if t["track"] == control]
    by_day: dict[str, list[float]] = {}
    for t in trades:
        by_day.setdefault(t["exit_date"], []).append(t["ret_net"])
    daily = [st.mean(v) for v in by_day.values()]
    mean = st.mean(t["ret_net"] for t in trades) if trades else None
    t = _t(daily)
    gate, usd, missing = _grade(track, len(by_day), t, mean, None,
                                extra_ok=len(trades) >= MIN[track]["trades"])
    return {"track": track, "gate": gate, "live_usd": usd, "missing": missing, "trades": len(trades),
            "days": len(by_day), "mean_trade_pct": None if mean is None else round(mean, 3),
            "t": None if t is None else round(t, 2),
            "vs_control_pp": round(mean - st.mean(x["ret_net"] for x in ctl), 3) if trades and ctl else None}


def curve_track(name: str, path) -> dict:
    rows = _jsonl(path)
    eq = [r["equity"] for r in rows]
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]
    mean = st.mean(rets) if rets else None
    t = _t(rets)
    dd = _max_dd(eq) if eq else None
    gate, usd, missing = _grade(name, len(rets), t, mean, dd)
    spy = rows[-1].get("spy_equiv") if rows else None
    return {"track": name, "gate": gate, "live_usd": usd, "missing": missing, "days": len(rets),
            "return_pct": round(100 * (eq[-1] / eq[0] - 1), 2) if len(eq) > 1 else 0.0,
            "vs_spy_pp": round(100 * (eq[-1] / spy - 1), 2) if spy and eq else None,
            "t": None if t is None else round(t, 2), "max_dd_pct": None if dd is None else round(dd, 2)}


def evaluate() -> dict:
    tracks = [rev1_track("rev1.S2", "rev1.C3"), rev1_track("rev1.S1", "rev1.C1"),
              curve_track("trend1", PAPER / "trend1" / "equity.jsonl"),
              curve_track("mom1", PAPER / "mom1" / "equity.jsonl"),
              curve_track("wheel1", PAPER / "wheel1" / "equity.jsonl")]
    best = max(tracks, key=lambda x: x["live_usd"])
    out = {"tracks": tracks, "best": best["track"] if best["live_usd"] else None,
           "live_usd": best["live_usd"]}
    OUT.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    r = evaluate()
    for t in r["tracks"]:
        print(f"{t['track']:<8} {t['gate']:<11} ${t['live_usd']:>5}  days {t['days']:>3}  t {t['t']}  "
              + ("; ".join(t["missing"]) or "all requirements met"))
    print(f"fundable now: {r['best'] or 'none'} (${r['live_usd']})")
