"""The exit plan written down AT ENTRY, and judged against later.

WHY THIS EXISTS

Two gaps, both found by asking "before a buy, what exit is planned?".

FIRST — nothing was recorded. `build_exit_plan()` produced a plan, the plan went
into `trade_plan.json` for a human to read, and the fill recorded symbol,
quantity, price and sleeve. The exit monitor then RECOMPUTED a plan from current
policy on every tick. So if policy changed, every open position was
retroactively judged by rules that did not exist when it was opened, and "did we
honour the plan" was unanswerable because no plan was ever stored.

SECOND — there was no loss exit. Every position classifies as `core`, and core
policy sets `max_loss_pct: None`: the only downside exit is theme invalidation,
a thesis-level signal that moves in weeks. A name could fall 30% with nothing
firing. Profit-taking was mechanical (scale out at +10/+20/+35%) and
loss-taking was discretionary — asymmetric in the dangerous direction, and
defensible only for multi-year conviction holds rather than for swing trading.

WHAT THE STOP IS BASED ON

Not a round number. Each name's own adverse-excursion distribution, measured
over years of daily bars by `mae_study`: the stop sits outside the move 80% of
that name's entries survived. That is why MRVL gets ~16% and CVX ~9% — a 15%
stop fires on 17% of MRVL entries and 5% of CVX's. One global number is
simultaneously hair-trigger and decorative depending on the name.

WHAT IT DELIBERATELY DOES NOT DO

It does not tune the stop to whatever would have made the most money. Sweeping
stop values and keeping the best is fitting, and this project has already
measured what that costs. The percentile is fixed in `mae_study` before any
outcome is examined, and this module reads it.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from ..config import ROOT

PATH = ROOT / "data" / "position_plans.json"

# Fallback when a name has too little history for its own distribution. Wide on
# purpose: a stop guessed for an unmeasured name should err toward not firing,
# because a stop inside the noise converts ordinary movement into realised loss.
FALLBACK_STOP_PCT = 25.0


def _load() -> dict[str, Any]:
    if not PATH.exists():
        return {}
    try:
        return json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(d: dict[str, Any]) -> None:
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, indent=2, default=str), encoding="utf-8")
        tmp.replace(PATH)
    except OSError:
        pass


def stop_for(symbol: str) -> tuple[float, str]:
    """(stop %, why) from this name's own measured excursions."""
    try:
        from ..analytics.mae_study import study_symbol
        s = study_symbol(symbol)
        if s.get("available") and s.get("suggested_stop_pct"):
            return float(s["suggested_stop_pct"]), (
                f"p{s.get('hold_sessions', 21)}-session MAE over {s['entries']} "
                f"entries; median {s['median_mae_pct']}%")
    except Exception:
        pass
    return FALLBACK_STOP_PCT, "insufficient history — wide fallback, not a measurement"


def targets_for(symbol: str, pool: str, theme_state: str | None) -> list[dict]:
    """Scale-out ladder from policy. exit_plans.py owns these numbers."""
    try:
        from ..analytics.exit_plans import build_exit_plan
        plan = build_exit_plan(symbol, "buy", pool=pool, theme_state=theme_state)
        so = (plan.get("staircase") or {}).get("scale_out") or {}
        gains = so.get("gain_pct_from_cost") or []
        fracs = so.get("fractions") or []
        return [{"gain_pct": float(g), "trim_fraction": float(fracs[i])
                 if i < len(fracs) else None}
                for i, g in enumerate(gains)]
    except Exception:
        return []


def record(symbol: str, entry_price: float, quantity: float, *,
           strategy: str = "unknown", pool: str = "core",
           theme_state: str | None = None) -> dict[str, Any]:
    """Write the plan at entry. Never overwrites an existing open plan.

    Not overwriting matters: an add to an existing position must not silently
    reset the stop to a level derived from the newer, higher entry. That would
    move the exit up behind the position every time it was topped up.
    """
    plans = _load()
    if symbol in plans and plans[symbol].get("open"):
        return plans[symbol]

    stop_pct, why = stop_for(symbol)
    plan = {
        "symbol": symbol,
        "opened": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "entry_price": round(float(entry_price), 4),
        "quantity": round(float(quantity), 6),
        "strategy": strategy,
        "pool": pool,
        "open": True,
        "stop_pct": stop_pct,
        "stop_price": round(float(entry_price) * (1 - stop_pct / 100.0), 4),
        "stop_basis": why,
        "targets": targets_for(symbol, pool, theme_state),
        "theme_state_at_entry": theme_state,
        "note": ("stop from this name's own excursion distribution, not a round "
                 "number; targets are exit_plans.py policy"),
    }
    plans[symbol] = plan
    _save(plans)
    return plan


def close(symbol: str, reason: str = "") -> None:
    plans = _load()
    if symbol in plans:
        plans[symbol]["open"] = False
        plans[symbol]["closed"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        plans[symbol]["close_reason"] = reason
        _save(plans)


def get(symbol: str) -> dict[str, Any] | None:
    p = _load().get(symbol)
    return p if p and p.get("open") else None


def all_open() -> dict[str, Any]:
    return {s: p for s, p in _load().items() if p.get("open")}


def report() -> dict[str, Any]:
    open_plans = all_open()
    return {
        "agent": "position_plans",
        "open": len(open_plans),
        "plans": open_plans,
        "does_not": [
            "tune the stop to what would have made the most money — the "
            "percentile is fixed before outcomes are examined",
            "reset an existing stop when a position is added to",
        ],
    }
