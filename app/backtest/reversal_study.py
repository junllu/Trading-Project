"""Short-term reversal v3 — tested ONLY on names no study has examined.

    python -m app.backtest.reversal_study

Implements docs/prereg/2026-09-27-reversal-v3.md exactly. The hypothesis came
out of v1/v2's results, so the 312 names those studies used cannot test it;
the 221 holdout names are the one clean sample left, and this is its single
use. Re-running this after reading the result and adjusting anything is a new
study with a new trial count.

Direction is REVERSED from v1/v2: a breakdown is scored as a long (the bounce),
a breakout as an avoid/trim (the fade). Everything else — triggers, conditions,
next-open entry, excess over the same-day universe, cost, block t-stats — is
reused unchanged so the only thing that differs is the hypothesis.
"""
from __future__ import annotations

import json
import math
import statistics as st
from dataclasses import asdict, dataclass

from ..config import ROOT
from .breakout_regime_study import PREREG, events_for
from .breakout_study import Event
from .costs import CostModel
from .trials import Trial, record

OUT_PATH = ROOT / "data" / "research" / "reversal_study.json"

# (id, trigger, conditions, direction, horizon). +1 = long the bounce,
# -1 = the name underperforms (avoid / trim).
HYPOTHESES = [
    ("R1", "bd20", (), +1, 5),
    ("R2", "bd20", (), +1, 10),
    ("R3", "bd55", (), +1, 5),
    ("R4", "bd55", ("relstr", "market"), +1, 10),
    ("F1", "bo20", (), -1, 5),
    ("F2", "bo20", ("trend", "squeeze"), -1, 5),
]
PASS_T = 2.4
ERA_T = 1.0
MIN_BLOCKS = 30
ERA_A_START = "2023-01-01"
ERA_B_END = "2022-12-31"
PURGE_SESSIONS = 20


@dataclass
class Result:
    id: str
    n: int = 0
    blocks: int = 0
    mean_net: float = 0.0
    mean_net_3x: float = 0.0
    hit: float = 0.0
    t: float = 0.0


def score(evs: list[Event], hid: str, trig: str, conf: tuple[str, ...], d: int, h: int,
          xs: dict, didx: dict, cost: float) -> Result:
    r = Result(hid)
    vals = []
    for e in evs:
        if e.trigger != trig or h not in e.fwd or not set(conf) <= e.confirms:
            continue
        base = xs.get((e.date, h))
        if base is not None:
            vals.append((e.date, d * (e.fwd[h] - base)))
    r.n = len(vals)
    if not vals:
        return r
    net = [v - cost for _, v in vals]
    r.mean_net = st.mean(net)
    r.mean_net_3x = st.mean(v - 3 * cost for _, v in vals)
    r.hit = sum(1 for v in net if v > 0) / len(net)
    blocks: dict[int, list[float]] = {}
    for (dt, _), v in zip(vals, net):
        blocks.setdefault(didx[dt] // h, []).append(v)
    bm = [st.mean(v) for v in blocks.values()]
    r.blocks = len(bm)
    if len(bm) >= 3 and st.stdev(bm) > 0:
        r.t = st.mean(bm) / (st.stdev(bm) / math.sqrt(len(bm)))
    return r


def run(record_trials: bool = True) -> dict:
    uni = json.loads(PREREG.read_text("utf-8"))
    cost = CostModel.retail_equity().round_trip_bps() / 100.0
    evs, xs, didx, dates = events_for(uni["holdout_names"])
    last_b = max(d for d in dates if d <= ERA_B_END)
    cut_b = dates[dates.index(last_b) - PURGE_SESSIONS]
    era_b = [e for e in evs if e.date <= cut_b]
    era_a = [e for e in evs if e.date >= ERA_A_START]

    rows = []
    for hid, trig, conf, d, h in HYPOTHESES:
        full = score(evs, hid, trig, conf, d, h, xs, didx, cost)
        a = score(era_a, hid, trig, conf, d, h, xs, didx, cost)
        b = score(era_b, hid, trig, conf, d, h, xs, didx, cost)
        passed = (full.blocks >= MIN_BLOCKS and full.mean_net > 0 and full.t >= PASS_T
                  and a.mean_net > 0 and a.t >= ERA_T and b.mean_net > 0 and b.t >= ERA_T)
        rows.append({"id": hid, "trigger": trig, "conditions": list(conf),
                     "direction": d, "horizon": h, "full": asdict(full),
                     "era_a": asdict(a), "era_b": asdict(b), "passed": passed})
        if record_trials:
            record(Trial(strategy="reversal_v3", sharpe=round(full.t, 3),
                         params={"trigger": trig, "conditions": list(conf),
                                 "direction": d, "horizon": h},
                         n_periods=full.blocks, window=f"{dates[0]}..{dates[-1]} holdout names",
                         note=f"prereg 2026-09-27-reversal-v3; passed={passed}"))
    out = {"prereg": "docs/prereg/2026-09-27-reversal-v3.md",
           "names": len(uni["holdout_names"]), "events": len(evs),
           "window": f"{dates[0]}..{dates[-1]}", "era_b": f"..{cut_b}",
           "cost_pct": cost, "pass_t": PASS_T, "results": rows,
           "passed": [r["id"] for r in rows if r["passed"]]}
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def _fmt(tag: str, s: dict) -> str:
    return (f"{tag:<7} n={s['n']:>5} blk={s['blocks']:>4} net={s['mean_net']:+7.3f}% "
            f"3x={s['mean_net_3x']:+7.3f}% hit={100 * s['hit']:5.1f}% t={s['t']:+6.2f}")


def _main() -> None:
    r = run()
    print("=" * 92)
    print(f"REVERSAL v3 — {r['names']} holdout names never examined, {r['events']:,} events")
    print(f"window {r['window']}   pass: full t >= {r['pass_t']} and both eras positive (t >= 1)")
    print("=" * 92)
    for x in r["results"]:
        side = "long bounce" if x["direction"] > 0 else "avoid/trim"
        cond = "+" + "+".join(x["conditions"]) if x["conditions"] else ""
        print(f"\n{x['id']}  {x['trigger']}{cond} @{x['horizon']}  ({side})  "
              f"{'PASS' if x['passed'] else 'fail'}")
        print("  " + _fmt("full", x["full"]))
        print("  " + _fmt("2023+", x["era_a"]))
        print("  " + _fmt("<=2022", x["era_b"]))
    print(f"\nPASSED: {', '.join(r['passed']) or 'none'}")


if __name__ == "__main__":
    _main()
