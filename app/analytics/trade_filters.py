"""Gates between conviction and a live/paper order intent.

Sleeve ladder (capital) and theme state (selection) are applied HERE so the
daily agent and trade_plan cannot size into a name the evidence forbids.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class FilterResult:
    allow: bool
    reason: str = ""
    sleeve_usd: int = 0
    theme_state: str | None = None


def sleeve_allows_live_buys() -> FilterResult:
    """Live BUY intents only when recommended_sleeve_usd > 0."""
    try:
        from .sleeve import STATUS_PATH, evaluate_sleeve, save_status
        import json
        if STATUS_PATH.exists():
            data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        else:
            status = evaluate_sleeve()
            save_status(status)
            data = status.to_dict()
        usd = int(data.get("recommended_sleeve_usd") or 0)
        gate = data.get("gate") or "NO DATA"
        if usd <= 0:
            return FilterResult(
                False,
                f"sleeve gate {gate}: live buys blocked (paper shadow "
                f"${int(data.get('paper_shadow_usd') or 0)})",
                sleeve_usd=0,
            )
        return FilterResult(True, f"sleeve gate {gate}: live ${usd}", sleeve_usd=usd)
    except Exception as exc:
        return FilterResult(False, f"sleeve unavailable ({exc}) — block live buys", 0)


def theme_allows_buy(symbol: str) -> FilterResult:
    """Block new buys into DEAD / ROTATING_OUT / NO_DATA themes when known."""
    try:
        from ..intel.themes import ALIVE, FADING, DEAD, ROTATING_OUT, NO_DATA, THEMES, report
        member_of = {m: n for n, ms in THEMES.items() for m in ms}
        theme = member_of.get(symbol.upper())
        if not theme:
            return FilterResult(True, "no theme mapping — pass", theme_state=None)
        states = report().get("themes") or {}
        st = (states.get(theme) or {}).get("state")
        if st in (DEAD, ROTATING_OUT):
            return FilterResult(
                False, f"theme {theme} is {st} — no new buys", theme_state=st)
        if st == NO_DATA:
            return FilterResult(
                False, f"theme {theme} is NO_DATA — no new buys until onboarded",
                theme_state=st)
        # ALIVE / FADING ok
        return FilterResult(True, f"theme {theme} is {st}", theme_state=st)
    except Exception as exc:
        return FilterResult(True, f"theme check skipped ({exc})", None)


def filter_buy(symbol: str, *, require_live_sleeve: bool = True) -> FilterResult:
    """Combined buy gate: sleeve (capital) then theme (selection)."""
    if require_live_sleeve:
        s = sleeve_allows_live_buys()
        if not s.allow:
            return s
    t = theme_allows_buy(symbol)
    if not t.allow:
        return t
    reason = s.reason if require_live_sleeve else t.reason
    if require_live_sleeve:
        reason = f"{s.reason}; {t.reason}"
    return FilterResult(True, reason, getattr(s, "sleeve_usd", 0) if require_live_sleeve else 0,
                        t.theme_state)


def discovery_candidates(limit: int = 10) -> list[dict[str, Any]]:
    """Best-effort Tier-1 discovery list for the plan (no MCP)."""
    try:
        from .discovery import scan, report
        try:
            rows = scan()
            out = []
            for c in rows[:limit]:
                if hasattr(c, "to_dict"):
                    out.append(c.to_dict())
                elif isinstance(c, dict):
                    out.append(c)
                else:
                    out.append({"symbol": getattr(c, "symbol", str(c))})
            return out
        except Exception:
            rep = report()
            if isinstance(rep, dict):
                for key in ("candidates", "top", "names"):
                    if key in rep and isinstance(rep[key], list):
                        return list(rep[key])[:limit]
                return [rep]
    except Exception:
        return []
    return []


def reentry_alerts(limit: int = 10) -> list[dict[str, Any]]:
    try:
        from ..intel.exit_discipline import reentry_candidates
        return reentry_candidates()[:limit]
    except Exception:
        return []
