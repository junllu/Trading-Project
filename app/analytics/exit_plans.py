"""Per-order exit plans — what 'done' means before an order is approved.

Core (thesis / focus) exits are invalidation + soft campaign trim + hard halt.
Tactical (agentic sleeve) adds time-stop and max-loss. Day-trader %-stops are
deliberately NOT the default on core: historical forgone gains came from
selling winners too early, not from missing a tight stop.
"""
from __future__ import annotations

from typing import Any


CORE = "core"
TACTICAL = "tactical"


def classify_pool(symbol: str, *, focus: set[str] | None = None,
                  sleeve_usd: int = 0) -> str:
    """Focus names (and anything sized only after sleeve clears) stay core unless
    the live sleeve is the funding path and the name is NOT a focus hold."""
    focus = focus or set()
    sym = symbol.upper()
    if sym in focus:
        return CORE
    # Non-focus buys that only exist because the tactical sleeve funded them
    if sleeve_usd > 0:
        return TACTICAL
    return CORE


def build_exit_plan(
    symbol: str,
    side: str,
    *,
    pool: str = CORE,
    theme_state: str | None = None,
    derisk_factor: float = 1.0,
    trailing_halt_pct: float | None = 0.20,
    time_stop_sessions: int = 21,
    max_loss_pct: float = 15.0,
    book_drawdown_pct: float | None = None,
    sleeve_edge_pp: float | None = None,
) -> dict[str, Any]:
    """Stamp onto a BUY (or SELL review). SELLs get a short 'why exiting' plan."""
    side = (side or "").lower()
    halt = trailing_halt_pct if trailing_halt_pct is not None else 0.20

    if side == "sell":
        return {
            "pool": pool,
            "kind": "exit_now",
            "invalidation": "n/a — this intent IS the exit",
            "soft_trim": f"campaign derisk_factor={derisk_factor:.3f} already applied to size",
            "hard_halt": f"book trailing drawdown ≥ {halt:.0%} → no new risk",
            "reentry_if": "theme returns to ALIVE and name appears on reentry_alerts",
            "time_stop_sessions": None,
            "max_loss_pct": None,
            "notes": [
                "Confirm shares held live before selling.",
                "If theme is still ALIVE, prefer trim/rotate over full liquidation.",
                "Prefer staircase scale-out over all-out liquidation when theme ALIVE.",
            ],
            "staircase": staircase_rules(
                pool, theme_state=theme_state,
                book_drawdown_pct=book_drawdown_pct, sleeve_edge_pp=sleeve_edge_pp),
        }

    # BUY
    plan: dict[str, Any] = {
        "pool": pool,
        "kind": "open_or_add",
        "invalidation": (
            "theme DEAD or ROTATING_OUT, or thesis_ledger checkpoint fail on next filing"
        ),
        "soft_trim": (
            f"scale with campaign derisk_factor (now {derisk_factor:.3f}) as deadline approaches"
        ),
        "hard_halt": f"book trailing drawdown ≥ {halt:.0%} from peak → halt new risk",
        "reentry_if": "if sold while theme ALIVE → keep on reentry watch; do not orphan",
        "time_stop_sessions": None,
        "max_loss_pct": None,
        "theme_state_at_entry": theme_state,
        "notes": [
            "Core default: hold through noise; exit on thesis/theme invalidation, not RSI.",
            "Staircase: scale out into strength; scale in only if theme+portfolio gates pass.",
        ],
        "staircase": staircase_rules(
            pool, theme_state=theme_state,
            book_drawdown_pct=book_drawdown_pct, sleeve_edge_pp=sleeve_edge_pp),
    }
    if pool == TACTICAL:
        plan["time_stop_sessions"] = time_stop_sessions
        plan["max_loss_pct"] = max_loss_pct
        plan["notes"] = [
            f"Tactical: flat by {time_stop_sessions} sessions unless thesis upgraded to core.",
            f"Tactical: cut if loss from entry ≥ {max_loss_pct:.0f}% (sleeve capital preservation).",
            "Do not promote tactical size without sleeve gate EVIDENCED+.",
        ]
    return plan


def reentry_to_action(row: dict[str, Any], *, focus: set[str] | None = None) -> dict[str, Any]:
    """Turn a reentry alert into a review row (NOT an auto-order)."""
    focus = focus or set()
    sym = row.get("symbol", "")
    return {
        "type": "reentry_review",
        "symbol": sym,
        "side": "buy",
        "auto_order": False,
        "focus": sym in focus,
        "theme": row.get("theme"),
        "sold_on": row.get("sold_on"),
        "sold_at": row.get("sold_at"),
        "now": row.get("now"),
        "move_pct": row.get("move_pct"),
        "forgone_now": row.get("forgone_now"),
        "action_required": "review — theme still ALIVE after exit; decide re-enter or close the watch",
        "exit_plan": build_exit_plan(
            sym, "buy",
            pool=CORE if sym in focus else TACTICAL,
            theme_state="ALIVE",
        ),
    }



# --- staircase scale-out / scale-in -----------------------------------------
# Philosophy: take partial profits and allow limited re-adds while the BOOK is
# advancing. Never average into a dead theme. Mistakes are budgeted at the
# sleeve/portfolio level, not unlimited per-name "conviction".

DEFAULT_SCALE_OUT = (0.33, 0.33, 0.34)          # fractions of position
DEFAULT_SCALE_OUT_GAINS = (0.10, 0.20, 0.35)    # +10% / +20% / +35% from cost
DEFAULT_SCALE_IN_DROPS = (0.08, 0.16)           # -8% / -16% from last add / HWM entry
DEFAULT_SCALE_IN_FRAC = (0.25, 0.25)            # each add ≤ 25% of original target
MAX_ADDS = 2                                   # hard cap on down-averages


def portfolio_ok_to_add(
    *,
    book_drawdown_pct: float | None = None,
    sleeve_edge_pp: float | None = None,
    max_book_dd_to_add: float = 0.10,
) -> tuple[bool, str]:
    """May we put NEW risk on (staircase reentry / down-average)?

    Overall portfolio moving up ≈ drawdown from peak shallow, OR sleeve still
    showing forward edge. Either signal is enough; both failing blocks adds.
    """
    dd = book_drawdown_pct
    if dd is not None and dd <= max_book_dd_to_add:
        return True, f"book DD {dd:.1%} ≤ {max_book_dd_to_add:.0%} — adds allowed"
    if sleeve_edge_pp is not None and sleeve_edge_pp > 0:
        return True, f"sleeve edge {sleeve_edge_pp:+.2f} pp > 0 — adds allowed"
    if dd is None and sleeve_edge_pp is None:
        return True, "no portfolio telemetry — allow with caution (paper/default)"
    return False, (
        f"block adds: book DD {dd!r} and sleeve edge {sleeve_edge_pp!r} "
        f"do not support averaging down"
    )


def staircase_rules(
    pool: str,
    *,
    theme_state: str | None = None,
    book_drawdown_pct: float | None = None,
    sleeve_edge_pp: float | None = None,
) -> dict:
    """Scale-out always available; scale-in only if theme + portfolio allow."""
    deadish = (theme_state or "").upper() in {"DEAD", "ROTATING_OUT", "NO_DATA"}
    ok, why = portfolio_ok_to_add(
        book_drawdown_pct=book_drawdown_pct, sleeve_edge_pp=sleeve_edge_pp)

    scale_out = {
        "enabled": True,
        "fractions": list(DEFAULT_SCALE_OUT),
        "gain_pct_from_cost": [g * 100 for g in DEFAULT_SCALE_OUT_GAINS],
        "note": "Take partial profits into strength; leave a runner for thesis holds.",
    }
    scale_in = {
        "enabled": (not deadish) and ok and pool in {CORE, TACTICAL},
        "max_adds": MAX_ADDS,
        "drop_pct_from_entry": [d * 100 for d in DEFAULT_SCALE_IN_DROPS],
        "fraction_of_original_each": list(DEFAULT_SCALE_IN_FRAC),
        "blocked_if_theme": ["DEAD", "ROTATING_OUT", "NO_DATA"],
        "portfolio_gate": why,
        "note": (
            "Down-average only while theme is ALIVE/FADING and portfolio gate passes. "
            "Budget mistakes at the sleeve — not unlimited per name."
        ),
    }
    if deadish:
        scale_in["enabled"] = False
        scale_in["portfolio_gate"] = f"theme {theme_state} — no averaging down"
    return {"scale_out": scale_out, "scale_in": scale_in}
