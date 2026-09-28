"""The user's own discretionary trade plans - alert-only, one-line messages.

    python -m app.paper.trade_plans check PLAN_ID SPOT BID ASK HELD [AVG_COST]

Nothing here trades. The user places these trades manually in their own
accounts; this only watches a written plan and says, briefly, when a level in
it is hit. Each alert fires at most once per day per kind, so an hourly check
does not repeat itself.

A plan is written BEFORE entry (entry zone, target, stop, event date) because
the user's last win on this contract came from the exit discipline, and the
most expensive habit in their record was deciding exits in the moment.
"""
from __future__ import annotations

import json
import sys
from datetime import date

from ..config import ROOT

STATE = ROOT / "data" / "paper" / "watch" / "trade_plans_state.json"

PLANS = {
    "googl-340c-2709": {
        "label": "GOOGL $340C Sep-27",
        "instrument_id": "75b6f552-7da3-41b6-8ed0-ca24952788bd",
        "entry_max": 50.00,           # buy zone: ask at or below
        "take_profit_pct": 0.40,      # +40% on the option...
        "target_spot": 360.0,         # ...or GOOGL at the 9/22 resistance
        "stop_spot": 325.0,           # GOOGL below the 9/9-9/10 support
        "event": ("2026-10-28", "earnings"),
        "event_warn_days": 2,
    },
}


def _state() -> dict:
    try:
        return json.loads(STATE.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def check(plan_id: str, spot: float, bid: float, ask: float, held: bool,
          avg_cost: float | None = None, today: date | None = None) -> list[str]:
    p, today = PLANS[plan_id], today or date.today()
    st = _state()
    fired = st.setdefault(plan_id, {})
    msgs: list[tuple[str, str]] = []
    if not held:
        if 0 < ask <= p["entry_max"]:
            msgs.append(("entry", f"{p['label']} in your buy zone: ${ask:.2f} ask (GOOGL ${spot:.0f}). "
                                  f"Plan: target ${p['target_spot']:.0f}, stop <${p['stop_spot']:.0f}."))
    else:
        cost = avg_cost or p["entry_max"]
        gain = bid / cost - 1 if cost else 0.0
        if gain >= p["take_profit_pct"] or spot >= p["target_spot"]:
            msgs.append(("target", f"{p['label']} hit target: bid ${bid:.2f} ({gain:+.0%}), GOOGL ${spot:.0f}. "
                                   f"Plan says sell into strength."))
        if spot < p["stop_spot"]:
            msgs.append(("stop", f"{p['label']}: GOOGL ${spot:.0f} is below your ${p['stop_spot']:.0f} stop "
                                 f"(bid ${bid:.2f}, {gain:+.0%}). Plan: exit if it closes there."))
        ev_day, ev = p["event"]
        days = (date.fromisoformat(ev_day) - today).days
        if 0 <= days <= p["event_warn_days"]:
            msgs.append(("event", f"{p['label']}: {ev} {ev_day[5:]} in {days}d. Decide now: close, or hold "
                                  f"a size you'd accept gapping 25-30% (bid ${bid:.2f}, {gain:+.0%})."))
    out = []
    for kind, text in msgs:
        if fired.get(kind) != today.isoformat():
            fired[kind] = today.isoformat()
            out.append(text)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, indent=1), encoding="utf-8")
    return out


if __name__ == "__main__":
    _, _, pid, spot, bid, ask, held, *rest = sys.argv
    for m in check(pid, float(spot), float(bid), float(ask), held == "1",
                   float(rest[0]) if rest else None):
        print(m)
