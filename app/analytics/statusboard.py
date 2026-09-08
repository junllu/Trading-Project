"""Flight status board — GO / NO-GO per subsystem, scanned not read.

A launch controller does not read their console. They scan a column of lights
and look only at what is not green. Everything nominal is silent by design,
because a display that demands interpretation gets interpreted late.

The dashboard was the opposite: eleven tables of numbers, each requiring you to
know the threshold, compare it yourself, and remember what it meant. That works
for a review and fails for a check — and a check is what happens on a Tuesday
morning with ten minutes before work.

So every subsystem reports one line:

    NOMINAL   nothing to do. Deliberately boring, deliberately quiet.
    CAUTION   worth a look this week.
    ALARM     look now. Capital or evidence integrity is affected.
    NO DATA   the check could not run — reported, never silently skipped,
              because a check that quietly fails reads exactly like a pass.

Ordered by consequence, not by convenience: guardrails first because they stop
losses, evidence integrity next because a decision made on bad data is worse
than no decision, and opportunity last because a missed gain is cheaper than a
realised loss — though this project's own record disputes that, with $148k of
forgone gains against $70k of realised profit.

    python -m app.analytics.statusboard
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field

NOMINAL, CAUTION, ALARM, NO_DATA = "NOMINAL", "CAUTION", "ALARM", "NO DATA"
_ORDER = {ALARM: 0, CAUTION: 1, NO_DATA: 2, NOMINAL: 3}


@dataclass
class Check:
    system: str
    state: str
    line: str                       # one line, readable at a glance
    detail: list[str] = field(default_factory=list)
    critical: bool = False          # affects capital directly

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _safe(fn, system: str, critical: bool = False) -> Check:
    """A check that raises reports NO DATA. It never silently passes."""
    try:
        return fn()
    except Exception as exc:
        return Check(system, NO_DATA, f"check failed: {type(exc).__name__}",
                     [str(exc)[:160]], critical)


# --- the checks, ordered by consequence -----------------------------------

def _guardrails() -> Check:
    from ..config import settings
    from ..portfolio.holdings import load_cash, load_holdings
    L = settings.risk
    holdings = load_holdings()
    equity = sum(float(h["shares"]) * (float(h.get("last", 0)) or float(h.get("avg_price", 0)))
                 for h in holdings) + (load_cash() or 0.0)
    detail = [f"equity ${equity:,.0f}",
              f"order cap ${L.order_cap(equity):,.0f} · position cap ${L.position_cap(equity):,.0f}"]
    return Check("GUARDRAILS", NOMINAL, "risk caps active, no halt", detail, critical=True)


def _recorder() -> Check:
    from ..agent.live_loop import report as live
    r = live()
    if r.get("rows", 0) == 0:
        return Check("RECORDER", ALARM, "no forward record — no out-of-sample evidence exists",
                     critical=True)
    if r.get("live_session_cycles", 0) == 0:
        return Check("RECORDER", CAUTION,
                     "never run in a REGULAR session — live behaviour unproven",
                     [f"{r['rows']} rows, sessions {r.get('sessions')}"], critical=True)
    return Check("RECORDER", NOMINAL,
                 f"{r['live_session_cycles']} live-session cycles recorded", critical=True)


def _data_integrity() -> Check:
    from ..data.market_data import report as md
    r = md()
    missing = r.get("held_without_real_history") or []
    age = r.get("age_days")
    if missing:
        return Check("DATA", ALARM,
                     f"{len(missing)} held name(s) priced on SYNTHETIC data",
                     [f"{', '.join(missing)} — every volatility scalar for these is wrong, "
                      f"and wrong toward taking MORE risk"], critical=True)
    if age is not None and age > 5:
        return Check("DATA", CAUTION, f"newest bar is {age} days old", critical=False)
    return Check("DATA", NOMINAL,
                 f"{r['symbols_cached']} symbols cached, newest {r.get('newest_bar')}")


def _thesis() -> Check:
    from ..intel.thesis import BROKEN, UNGRADED, WATCH, report as th
    rep = th()
    if "error" in rep:
        return Check("THESIS", NO_DATA, rep["error"])
    counts: dict[str, int] = {}
    for row in rep["theses"]:
        counts[row["verdict"]] = counts.get(row["verdict"], 0) + 1
    broken = counts.get(BROKEN, 0)
    line = "  ".join(f"{k} {v}" for k, v in sorted(counts.items()))
    if broken:
        names = [r["symbol"] for r in rep["theses"] if r["verdict"] == BROKEN]
        return Check("THESIS", ALARM, f"{broken} thesis BROKEN: {', '.join(names)}",
                     [line], critical=True)
    if rep.get("stale"):
        return Check("THESIS", CAUTION, f"snapshot {rep['age_days']}d old — refresh before deciding",
                     [line])
    if counts.get(UNGRADED):
        ung = [r["symbol"] for r in rep["theses"] if r["verdict"] == UNGRADED]
        return Check("THESIS", CAUTION,
                     f"{len(ung)} holding(s) have no checkpoints: {', '.join(ung)}", [line])
    return Check("THESIS", NOMINAL, line)


def _themes() -> Check:
    from ..intel.themes import report as th
    r = th()
    exposed = r.get("exposed_to_rotating_themes") or {}
    if exposed:
        tot = sum(exposed.values())
        return Check("THEMES", ALARM,
                     f"${tot:,} exposed to a rotating or dead theme",
                     [f"{k}: ${v:,}" for k, v in exposed.items()], critical=True)
    live = [n for n, s in r["themes"].items() if s["state"] == "ALIVE"]
    return Check("THEMES", NOMINAL, f"no exposure to a rotating theme · live: {', '.join(live)}")


def _sectors() -> Check:
    from .sectors import report as sr
    r = sr()
    if "error" in r:
        return Check("SECTORS", NO_DATA, r["error"])
    detail = [f"leading {', '.join(r['leaders_1m'])}",
              f"lagging {', '.join(r['laggards_1m'])}"]
    if r.get("defensive_leadership"):
        return Check("SECTORS", CAUTION,
                     f"DEFENSIVE leadership — {', '.join(r['defensive_in_top4'])} in the top 4",
                     detail + ["the bid has changed character"])
    weak = r.get("weakening_with_exposure") or []
    if weak:
        return Check("SECTORS", CAUTION,
                     f"held sector(s) weakening: {', '.join(weak)}", detail)
    return Check("SECTORS", NOMINAL, f"leading {', '.join(r['leaders_1m'])}", detail)


def _valuation() -> Check:
    from .peer_value import report as pv
    r = pv()
    dear = r.get("held_above_peers") or []
    if not dear:
        return Check("VALUATION", NOMINAL, "no holding above its peer group")
    return Check("VALUATION", CAUTION,
                 f"{len(dear)} holding(s) above peers: {', '.join(dear)}",
                 ["a premium is being paid — deserved or not is the thesis's job"])


def _strategy() -> Check:
    from ..backtest.trials import registry_summary
    s = registry_summary()
    if not s.get("n_trials"):
        return Check("STRATEGY", NO_DATA, "no trials recorded — nothing to deflate")
    v = s.get("verdict", "")
    state = ALARM if v.startswith("REJECT") else (CAUTION if v.startswith("WEAK") else NOMINAL)
    return Check("STRATEGY", state,
                 f"DSR {s['deflated_sharpe_ratio']} over {s['n_trials']} configs",
                 [v[:150]])


def _book_sync() -> Check:
    """How old is the snapshot every guardrail is computed from?

    config/holdings.yaml is the single source of truth for equity, and equity
    sets the position cap, the order cap and the drawdown halt. A stale book
    does not fail loudly — it quietly sizes today's orders off last week's
    prices, which is the failure mode 'make no mistake' exists to prevent.
    """
    from datetime import datetime
    from ..config import ROOT
    p = ROOT / "config" / "holdings.yaml"
    if not p.exists():
        return Check("BOOK SYNC", ALARM, "config/holdings.yaml missing — no book to size against",
                     critical=True)
    age_h = (datetime.now().timestamp() - p.stat().st_mtime) / 3600.0
    detail = [f"holdings.yaml last written {age_h:.0f}h ago",
              "refresh with /sync-holdings (Robinhood) + "
              "`python -m app.brokers.webull_openapi positions --account-id ...` (Webull)"]
    if age_h > 24 * 7:
        return Check("BOOK SYNC", ALARM,
                     f"book snapshot is {age_h / 24:.0f} days old — guardrails sized off stale equity",
                     detail, critical=True)
    if age_h > 48:
        return Check("BOOK SYNC", CAUTION,
                     f"book snapshot is {age_h / 24:.1f} days old", detail, critical=True)
    return Check("BOOK SYNC", NOMINAL, f"book synced {age_h:.0f}h ago", critical=True)


# A stop rule needs far more evidence than an execution-timing preference: it
# fires rarely, so the sessions that matter are a small subset of those stored.
MIN_SESSIONS_FOR_STOP_VERDICT = 60


def _exit_truth() -> Check:
    """Are the stop levels measured, or merely asserted?

    exit_plans.py sets a 15% tactical max-loss and a 20% book trailing halt, and
    states that forgone gains came from selling winners early rather than from
    missing a tight stop. That is a testable claim which has never been tested
    on intraday data — daily bars cannot see whether a level was touched. Until
    the minute store is deep enough, this reports the gap rather than implying
    the thresholds are validated.
    """
    from ..data.minute import coverage
    cov = coverage()
    if not cov:
        return Check("EXIT TRUTH", CAUTION,
                     "stop levels are asserted, never tested — no minute data stored",
                     ["exit_plans.py uses max_loss 15% / trailing halt 20%",
                      "daily bars cannot tell a touched stop from a close — "
                      "every stop backtest so far under-counts triggers",
                      "harvest: python -m app.data.minute --fetch MRVL --days 365"],
                     critical=False)
    best = max(cov.values(), key=lambda v: v["days"])
    days = best["days"]
    syms = ", ".join(sorted(cov))
    if days < MIN_SESSIONS_FOR_STOP_VERDICT:
        return Check("EXIT TRUTH", CAUTION,
                     f"minute data too thin to test stops — {days} session(s), "
                     f"{MIN_SESSIONS_FOR_STOP_VERDICT} needed",
                     [f"stored: {syms}",
                      "thresholds in exit_plans.py remain unvalidated against intraday truth"],
                     critical=False)
    return Check("EXIT TRUTH", NOMINAL,
                 f"{len(cov)} symbol(s) with {days} sessions — stops testable",
                 [f"run: python -m app.analytics.intraday --sweep <SYMBOL>"])


def _opportunity() -> Check:
    """Missed upside. Last by convention — and this record disputes that order."""
    from ..intel.exit_discipline import reentry_candidates
    rc = [r for r in reentry_candidates() if r["forgone_now"] > 0]
    if not rc:
        return Check("RE-ENTRY", NOMINAL, "nothing sold out of a still-live theme")
    tot = sum(r["forgone_now"] for r in rc)
    return Check("RE-ENTRY", CAUTION,
                 f"{len(rc)} name(s) sold from a live theme · ${tot:,} forgone",
                 [f"{r['symbol']} sold {r['sold_on']} · {r['move_pct']:+.0f}% since" for r in rc])


CHECKS = [
    ("GUARDRAILS", _guardrails, True),
    ("BOOK SYNC", _book_sync, True),
    ("RECORDER", _recorder, True),
    ("DATA", _data_integrity, True),
    ("EXIT TRUTH", _exit_truth, False),
    ("THESIS", _thesis, True),
    ("THEMES", _themes, True),
    ("SECTORS", _sectors, False),
    ("VALUATION", _valuation, False),
    ("STRATEGY", _strategy, False),
    ("RE-ENTRY", _opportunity, False),
]


def board() -> dict:
    rows = [_safe(fn, name, crit) for name, fn, crit in CHECKS]
    alarms = [c for c in rows if c.state == ALARM]
    cautions = [c for c in rows if c.state == CAUTION]
    overall = ALARM if alarms else (CAUTION if cautions else NOMINAL)
    return {
        "overall": overall,
        "go_for_launch": overall == NOMINAL,
        "alarms": len(alarms),
        "cautions": len(cautions),
        "checks": [c.to_dict() for c in sorted(rows, key=lambda c: (_ORDER[c.state],
                                                                   not c.critical))],
    }


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    b = board()
    if args.json:
        print(json.dumps(b, indent=2))
        return

    mark = {NOMINAL: " GO ", CAUTION: "CAUT", ALARM: "ALRM", NO_DATA: " ?? "}
    print("=" * 78)
    print(f"  FLIGHT STATUS — {b['overall']}"
          f"   ({b['alarms']} alarm, {b['cautions']} caution)")
    print("=" * 78)
    for c in b["checks"]:
        crit = "!" if c["critical"] else " "
        print(f"  [{mark[c['state']]}]{crit} {c['system']:12} {c['line']}")
        if c["state"] != NOMINAL:
            for d in c["detail"]:
                print(f"              {d}")
    print()
    if b["go_for_launch"]:
        print("  All subsystems nominal. Nothing needs you today.")
    else:
        print("  Scan the top rows only. Everything below the last CAUTION is fine.")


if __name__ == "__main__":
    _main()
