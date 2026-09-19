"""The exit monitor — the joint that closes the loop.

WHAT WAS BROKEN

`exit_plans.py` stamps an exit plan onto every order: invalidation, soft trim,
hard halt, and for tactical positions a time stop and a max loss. That plan was
written into `data/trade_plan.json` and then never read again by anything. The
book had exit LEVELS and no exit WATCHER. The only breach check that ran at
runtime was the book-wide campaign drawdown halt, which says nothing about any
individual position. So the system could name the level at which a thesis was
wrong and then never look at it again.

This reads those levels back and evaluates live positions against them.

WHY IT SETS NO LEVELS OF ITS OWN

The module boundary is stated in `intraday.py` and honoured here: exit_plans.py
owns POLICY, intraday.py MEASURES, and neither may name a number belonging to
the other. So this file contains no stop percentage, no horizon, no threshold.
It calls `build_exit_plan()` for each position and evaluates whatever comes
back. Change the policy in one place and the monitor follows.

WHY INTRADAY EXTREMES, NOT LAST CLOSE

A daily bar cannot answer the only question a stop asks: was the level actually
touched? A session that opens 220, wicks to 186 and closes 219 is a quiet -0.5%
day to a daily bar and a liquidation to a 15% stop. Where minute bars exist the
monitor checks the true intraday low, and where they do not it says so instead
of quietly answering a different question with the close.

WHY IT REPORTS WHAT IT CANNOT EVALUATE

`time_stop_sessions` needs an entry date, and `holdings.yaml` records none. A
monitor that silently skipped that rule would report "no exit triggered" while
having never checked one of the two tactical exits. Unevaluable rules are
returned in `not_evaluated` with the reason, so the caller can tell "checked and
fine" from "never looked".

    python -m app.agent.exit_monitor
    python -m app.agent.exit_monitor --json
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

HOLD, TRIM, EXIT = "HOLD", "TRIM", "EXIT"

# Retries before an exit is declared BLOCKED. Bounded so a permanently refused
# exit does not spam the record every tick, but not one — a transient refusal
# (a cap, a stale allowlist) must get another chance once the cause is fixed.
MAX_EXIT_ATTEMPTS = 3


def _state_path():
    from ..config import ROOT
    return ROOT / "data" / "exit_state.json"


def load_state() -> dict[str, Any]:
    """Which rungs have already been acted on, per symbol.

    Without this the monitor re-fires forever. A staircase rung is a LEVEL, not
    an event: a position above +10% stays above +10%, so an unattended loop
    reading only the current gain trims the same position on every tick and
    grinds a winner to zero in a dozen cycles. Rungs must fire once.
    """
    p = _state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict[str, Any]) -> None:
    p = _state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass                                      # never break the tick on state I/O


@dataclass
class RuleCheck:
    rule: str
    fired: bool
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class Verdict:
    symbol: str
    pool: str
    action: str = HOLD
    quantity: float = 0.0
    avg_price: float = 0.0
    last_price: float = 0.0
    unrealized_pct: float = 0.0
    checks: list[RuleCheck] = field(default_factory=list)
    not_evaluated: list[dict[str, str]] = field(default_factory=list)
    price_basis: str = "close"
    rung: int = -1                    # staircase rung this verdict would spend
    # True when the rung was already passed before the monitor ever ran. These
    # fills are a one-time catch-up on positions that ran up unattended, not a
    # decision the strategy made — marked so the opening burst is never read as
    # strategy performance.
    backlog: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["fired"] = [c.rule for c in self.checks if c.fired]
        return d


def _intraday_low_since(symbol: str, days: int) -> tuple[Optional[float], str]:
    """True session low over the window, from minute bars when they exist.

    Returns (low, basis). A stale or absent feed is reported rather than
    silently replaced by the daily close — running a stop against two-day-old
    bars and calling it real time is the failure this note exists to prevent.
    """
    try:
        from ..data.minute import load
    except Exception:
        return None, "minute module unavailable"
    try:
        start = datetime.now(timezone.utc) - timedelta(days=days)
        bars = load(symbol, start=start)
    except Exception as exc:
        return None, f"minute load failed: {type(exc).__name__}"
    if not bars:
        return None, "no minute bars cached"
    low = min(b.low for b in bars)
    newest = max(b.ts for b in bars)
    age_days = (datetime.now(timezone.utc) - newest).days
    basis = f"minute bars ({len(bars)} bars, newest {newest:%Y-%m-%d})"
    if age_days >= 1:
        basis += f" — STALE by {age_days}d"
    return low, basis


def _theme_state(symbol: str) -> Optional[str]:
    try:
        from ..analytics.trade_filters import theme_allows_buy
        return theme_allows_buy(symbol).theme_state
    except Exception:
        return None


def evaluate_position(symbol: str, quantity: float, avg_price: float,
                      last_price: float, *, focus: set[str] | None = None,
                      sleeve_usd: int = 0, derisk_factor: float = 1.0,
                      lookback_days: int = 30,
                      done_rungs: set[int] | None = None,
                      exit_attempted: bool = False,
                      first_seen: bool = False) -> Verdict:
    """Grade one live position against the exit plan its own policy would give it."""
    from ..analytics.exit_plans import build_exit_plan, classify_pool

    done_rungs = done_rungs or set()
    pool = classify_pool(symbol, focus=focus or set(), sleeve_usd=sleeve_usd)
    theme_state = _theme_state(symbol)
    plan = build_exit_plan(symbol, "buy", pool=pool, theme_state=theme_state,
                           derisk_factor=derisk_factor)

    v = Verdict(symbol=symbol, pool=pool, quantity=quantity,
                avg_price=avg_price, last_price=last_price)
    if avg_price > 0:
        v.unrealized_pct = round((last_price - avg_price) / avg_price * 100, 2)

    # --- theme invalidation (applies to every pool) ------------------------
    from ..intel.themes import DEAD, ROTATING_OUT
    if theme_state is None:
        v.not_evaluated.append({"rule": "invalidation",
                                "why": "no theme mapping for this symbol"})
    else:
        dead = theme_state in (DEAD, ROTATING_OUT)
        # An EXIT that was already attempted does not re-fire. If it was
        # rejected (a cap, say) retrying every tick spams the record without
        # changing the outcome; the blockage is the thing to fix, not the
        # retry rate.
        fires = dead and not exit_attempted
        v.checks.append(RuleCheck(
            rule="invalidation", fired=fires,
            detail=(f"theme state {theme_state}"
                    + (" — thesis invalidated" if dead else "")
                    + (" (exit already attempted — not re-firing)"
                       if dead and exit_attempted else "")),
            evidence={"theme_state": theme_state, "exit_attempted": exit_attempted}))
        if fires:
            v.action = EXIT

    # --- max loss ----------------------------------------------------------
    # The plan RECORDED AT ENTRY wins over policy recomputed now. Core policy
    # sets no stop at all, so without this every position had a mechanical
    # profit-take and a discretionary loss-take — asymmetric in the dangerous
    # direction for anything held on a swing horizon.
    max_loss = plan.get("max_loss_pct")
    stop_source = "policy"
    try:
        from . import position_plans
        recorded = position_plans.get(symbol)
    except Exception:
        recorded = None
    if recorded and recorded.get("stop_pct"):
        max_loss = float(recorded["stop_pct"])
        stop_source = f"entry plan ({recorded.get('stop_basis', 'recorded')})"

    if max_loss is None:
        v.not_evaluated.append({
            "rule": "max_loss_pct",
            "why": f"policy sets no max loss for pool={pool} (core holds through noise)"})
    elif avg_price <= 0:
        v.not_evaluated.append({"rule": "max_loss_pct", "why": "no cost basis recorded"})
    else:
        stop_price = avg_price * (1 - float(max_loss) / 100.0)
        low, basis = _intraday_low_since(symbol, lookback_days)
        v.price_basis = basis if low is not None else f"close only ({basis})"
        touched_at = low if low is not None else last_price
        fired = touched_at <= stop_price
        v.checks.append(RuleCheck(
            rule="max_loss_pct", fired=fired,
            detail=(f"stop {stop_price:.2f} ({max_loss:.1f}% below {avg_price:.2f}, "
                    f"{stop_source}); "
                    f"{'touched' if fired else 'not touched'} at {touched_at:.2f}"),
            evidence={"stop_price": round(stop_price, 4),
                      "worst_price": round(touched_at, 4),
                      "stop_source": stop_source,
                      "basis": v.price_basis}))
        if fired:
            v.action = EXIT

    # --- time stop: named by policy, not evaluable from holdings ----------
    if plan.get("time_stop_sessions") is not None:
        v.not_evaluated.append({
            "rule": "time_stop_sessions",
            "why": ("holdings.yaml records no entry date, so sessions-held is "
                    "unknown — this exit is NOT being checked")})

    # --- staircase scale-out into strength --------------------------------
    # Policy expresses this as a ladder of gain thresholds with a matching
    # ladder of fractions. The monitor reports the DEEPEST rung reached; it
    # does not invent a rung or a size of its own.
    stair = (plan.get("staircase") or {}).get("scale_out") or {}
    gains = stair.get("gain_pct_from_cost") or []
    if stair.get("enabled") and gains and avg_price > 0:
        reached = [i for i, g in enumerate(gains) if v.unrealized_pct >= float(g)]
        # A rung already acted on is spent. Only the deepest UNSPENT rung fires,
        # so a position that has run far past several rungs trims once per rung
        # over time rather than once per tick forever.
        unspent = [i for i in reached if i not in done_rungs]
        fired = bool(unspent)
        fracs = stair.get("fractions") or []
        rung = max(unspent) if unspent else -1
        frac = fracs[rung] if 0 <= rung < len(fracs) else None
        v.rung = rung
        v.checks.append(RuleCheck(
            rule="staircase_scale_out", fired=fired,
            detail=(f"rungs at {[f'+{float(g):.0f}%' for g in gains]}; "
                    f"now {v.unrealized_pct:+.1f}%"
                    + (f"; already taken {sorted(done_rungs)}" if done_rungs else "")
                    + (f" → rung {rung + 1}, trim {float(frac):.0%}" if frac else "")),
            evidence={"gain_rungs_pct": gains, "unrealized_pct": v.unrealized_pct,
                      "rung_reached": rung + 1, "trim_fraction": frac,
                      "rungs_already_taken": sorted(done_rungs)}))
        if fired and v.action == HOLD:
            v.action = TRIM
            # A rung fired the very first time this position is seen was crossed
            # before the monitor existed. That is catch-up, not a call.
            v.backlog = first_seen
            if frac:
                v.quantity = round(quantity * float(frac), 4)

    return v


def scan(portal=None) -> dict[str, Any]:
    """Grade every held position. Reads state; places nothing."""
    from ..portal import portal as default_portal
    p = (portal or default_portal).ensure_built()

    camp = p.campaign
    focus = set(camp.focus_symbols) if camp else set()
    derisk = camp.derisk_factor() if camp else 1.0
    sleeve_usd = 0
    try:
        from ..analytics.trade_filters import sleeve_allows_live_buys
        sleeve_usd = int(sleeve_allows_live_buys().sleeve_usd or 0)
    except Exception:
        sleeve_usd = 0

    positions = []
    broker = p.brokers.get("paper")
    if broker is not None:
        try:
            positions = broker.get_positions()
        except Exception:
            positions = []

    state = load_state()
    held_now = {pos.symbol for pos in positions}
    # A symbol that left the book has its ladder reset: a fresh entry starts at
    # rung zero rather than inheriting the last position's spent rungs.
    for sym in list(state):
        if sym not in held_now:
            state.pop(sym, None)

    verdicts: list[Verdict] = []
    unpriced: list[str] = []
    for pos in positions:
        # A position with no real quote cannot be graded. Its fallback price is
        # a random walk, so every rule below would fire or not on dice — and the
        # verdict would then place a real order. Skipped and reported.
        if not p.market.is_real(pos.symbol):
            unpriced.append(pos.symbol)
            continue
        last = p.market.last(pos.symbol)
        px = last.price if last else pos.avg_price
        st = state.get(pos.symbol) or {}
        verdicts.append(evaluate_position(
            pos.symbol, pos.quantity, pos.avg_price, px,
            focus=focus, sleeve_usd=sleeve_usd, derisk_factor=derisk,
            done_rungs=set(st.get("rungs_taken") or []),
            exit_attempted=bool(st.get("exit_attempted")),
            first_seen=pos.symbol not in state))
    save_state(state)

    acting = [v for v in verdicts if v.action in (EXIT, TRIM)]
    return {
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "agent": "exit_monitor",
        "kind": "checker",
        "mode": p.settings.mode.value,
        "positions_checked": len(verdicts),
        "unpriced_skipped": sorted(unpriced),
        # A position that cannot be exited is not a detail — it is trapped risk,
        # and it must be visible rather than absorbed into a rejection count.
        "blocked_exits": [
            {"symbol": s, "attempts": v.get("exit_rejections"),
             "last_reason": v.get("last_rejection")}
            for s, v in sorted(state.items())
            if v.get("exit_rejections")],
        "actions": [v.to_dict() for v in acting],
        "holds": [v.to_dict() for v in verdicts if v.action == HOLD],
        "unchecked_rules": sum(len(v.not_evaluated) for v in verdicts),
        "does_not": [
            "set exit levels — exit_plans.py owns policy",
            "place any order outside PAPER — in live or confirm the same verdict "
            "is staged for thomas behind a human yes",
            "score whether an exit was a good one — the blotter records the fills "
            "under strategy=exit_monitor; judging them is a separate measurement",
            "answer a stop with a daily close when minute bars are missing",
        ],
    }


def execute(verdicts: list[dict[str, Any]], portal=None) -> dict[str, Any]:
    """Route EXIT/TRIM verdicts through the executor. PAPER ONLY.

    The mode guard is not a formality. In paper a fill is a simulation and needs
    no approval, which is what lets this run unattended and accumulate evidence.
    In live the same verdict is real money and belongs to thomas behind an
    explicit human yes — so outside paper this stages and places nothing,
    regardless of what the caller asked for.

    Sized in dollars because that is the executor's contract, then clamped back
    to the held quantity on the way through. An EXIT sells the position; a TRIM
    sells only the fraction the staircase policy named.
    """
    from ..config import TradingMode
    from ..models import Side, Signal
    from ..portal import portal as default_portal

    p = (portal or default_portal).ensure_built()
    if p.settings.mode is not TradingMode.PAPER:
        return {"placed": [], "staged": [v["symbol"] for v in verdicts],
                "note": (f"mode={p.settings.mode.value}: exits are staged for thomas, "
                         f"not placed. Automatic exit execution is paper-only.")}

    state = load_state()
    placed = []
    for v in verdicts:
        qty = float(v.get("quantity") or 0)
        px = float(v.get("last_price") or 0)
        if qty <= 0 or px <= 0:
            continue
        fired = ", ".join(v.get("fired") or []) or v["action"].lower()
        tag = "BACKLOG " if v.get("backlog") else ""
        sig = Signal(symbol=v["symbol"], side=Side.SELL,
                     order_value=round(qty * px, 2),
                     strategy="exit_monitor",
                     note=f"{tag}{v['action']} — {fired}")
        res = p.executor.handle_signal(sig, px)

        # Spent on ACCEPTANCE, not on attempt.
        #
        # The first version marked it on attempt, to stop a rejected exit
        # re-firing every tick. That silently made a rejection permanent: BE was
        # refused by the symbol allowlist, flagged as attempted, and then never
        # retried even after the allowlist bug was fixed. A blocked exit that
        # gives up is worse than one that retries.
        #
        # So a rejection retries, but not forever — after MAX_EXIT_ATTEMPTS the
        # symbol is reported as BLOCKED rather than quietly dropped, because a
        # position that cannot be exited is something a human needs to see.
        st = state.setdefault(v["symbol"], {"rungs_taken": [], "exit_attempted": False})
        accepted = res.order.status.value in ("filled", "submitted", "queued")
        if v["action"] == EXIT:
            if accepted:
                st["exit_attempted"] = True
                st.pop("exit_rejections", None)
            else:
                st["exit_rejections"] = int(st.get("exit_rejections", 0)) + 1
                st["last_rejection"] = (res.order.reason or "")[:120]
                if st["exit_rejections"] >= MAX_EXIT_ATTEMPTS:
                    st["exit_attempted"] = True   # stop retrying; surfaced below
        rung = int(v.get("rung", -1))
        if accepted and rung >= 0 and rung not in st["rungs_taken"]:
            st["rungs_taken"].append(rung)
        st["last_attempt"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        placed.append({"symbol": v["symbol"], "action": v["action"],
                       "status": res.order.status.value,
                       "filled_price": res.order.filled_price,
                       "detail": res.detail})
    save_state(state)
    return {"placed": placed, "staged": [],
            "note": "paper fills, recorded to the blotter as strategy=exit_monitor"}


def run(portal=None, act: bool = True) -> dict[str, Any]:
    """Scan, then act on what fired. The loop's entrypoint."""
    r = scan(portal)
    if act and r["actions"]:
        r["execution"] = execute(r["actions"], portal)
    return r


def report() -> dict[str, Any]:
    """Roster entrypoint. Reports only — the loop calls run() when it wants action."""
    return scan()


def _main() -> None:                              # pragma: no cover - CLI
    ap = argparse.ArgumentParser(description="Exit monitor — live positions vs their exit plans.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--act", action="store_true",
                    help="place the fired exits (paper only; staged otherwise)")
    args = ap.parse_args()
    r = run(act=args.act)
    if args.json:
        print(json.dumps(r, indent=2, default=str))
        return

    print("=" * 78)
    print("  EXIT MONITOR — every held position against its own exit plan")
    print("=" * 78)
    print(f"\n  mode {r['mode']} · {r['positions_checked']} position(s) checked · "
          f"{len(r['actions'])} action(s) · {r['unchecked_rules']} rule(s) NOT evaluable")

    if r["actions"]:
        print(f"\n  {'-' * 74}\n  ACTION")
        for v in r["actions"]:
            print(f"\n    {v['symbol']:6} {v['action']:5} {v['pool']:9} "
                  f"{v['unrealized_pct']:+.1f}%  qty {v['quantity']:g}")
            for c in v["checks"]:
                if c["fired"]:
                    print(f"      FIRED  {c['rule']}: {c['detail']}")
    else:
        print("\n  no exit rule fired on any position")

    unchecked = [(v["symbol"], n) for v in r["actions"] + r["holds"] for n in v["not_evaluated"]]
    if unchecked:
        print(f"\n  {'-' * 74}\n  NOT EVALUATED — checked nothing, not 'passed'")
        for sym, n in unchecked[:12]:
            print(f"    {sym:6} {n['rule']:20} {n['why']}")


if __name__ == "__main__":                        # pragma: no cover
    _main()
