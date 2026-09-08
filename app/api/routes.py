"""REST API for the dashboard and any external control.

Endpoints are intentionally small and JSON-first so the same API backs the web
UI, a future mobile client, or a CLI.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..models import Order, OrderType, Side
from ..portal import portal

router = APIRouter(prefix="/api", tags=["portal"])


@router.get("/status")
def status():
    return portal.status()


@router.post("/tick")
def tick():
    """Run one strategy/execution cycle on demand."""
    return portal.tick()


@router.get("/portfolio")
def portfolio():
    return portal.status()["portfolio"]


@router.get("/quotes")
def quotes():
    symbols = portal._all_symbols()
    return {s: q.price for s, q in portal.market.refresh(symbols).items()}


# --- intelligence layer ----------------------------------------------------
@router.get("/intel")
def intel(force: bool = False, use_x: bool = False):
    """Current events briefing: Grok live-search + geopolitical assessment."""
    return portal.ensure_built().intel_briefing(force=force, use_x=use_x)


@router.get("/intel/ask")
def intel_ask(q: str):
    """Free-form geopolitical / market question to Grok (live if XAI_API_KEY set)."""
    return {"question": q, "answer": portal.ensure_built().intel.grok.ask(q)}


# --- options plans ---------------------------------------------------------
@router.get("/options/plans")
def options_plans(days: int = 30):
    """Covered-call, cash-secured-put, and sell-the-news suggestions for review."""
    return portal.ensure_built().option_plans(days=days)


# --- analytics + daily agent ----------------------------------------------
@router.get("/analytics")
def analytics():
    """Composite technical score per symbol."""
    return portal.ensure_built().analytics()


@router.post("/agent/run")
def agent_run():
    """Run the daily agent now: analyze, blend conviction, size, route orders."""
    return portal.ensure_built().run_daily()


@router.get("/agent/report")
def agent_report():
    """The most recent daily report (empty until the agent has run)."""
    return portal.ensure_built().last_report()


@router.get("/allocation")
def allocation():
    """Tiered 'pockets' allocation of the fund across ranked opportunities."""
    return portal.ensure_built().allocation_plan()


@router.get("/macro")
def macro(as_of: str | None = None):
    """Active policy-cycle regimes and sector tilts (optionally as of a date)."""
    return portal.ensure_built().macro_view(as_of)


@router.post("/agent/plan")
def agent_plan():
    """Emit a trade plan (order intents + guardrails) to data/trade_plan.json
    for execution through the Robinhood MCP by the local Claude."""
    return portal.ensure_built().build_trade_plan()


@router.post("/agent/schedule/start")
def agent_schedule_start():
    return portal.ensure_built().start_schedule()


@router.post("/agent/schedule/stop")
def agent_schedule_stop():
    return portal.ensure_built().stop_schedule()


@router.get("/symbol/{symbol}")
def symbol_dossier(symbol: str):
    """Every layer of analysis for one ticker: position, metrics, screens,
    structural macro facts, curated-source views, and cycle history."""
    from ..analytics.deep_dive import dossier
    portal.ensure_built()
    conv = {}
    rep = portal.daily_agent.last_report if portal.daily_agent else None
    if rep:
        conv = next((c for c in rep.convictions
                     if c.get("symbol", "").upper() == symbol.upper()), {})
    return dossier(symbol, conv)


# --- live loop (session-aware; records signals, never trades) ---------------
@router.get("/live/status")
def live_status():
    return portal.ensure_built().live_status()


@router.post("/live/start")
def live_start():
    return portal.ensure_built().start_live()


@router.post("/live/stop")
def live_stop():
    return portal.ensure_built().stop_live()


@router.post("/live/cycle")
def live_cycle_now():
    """Force one evaluation regardless of session. Records; places nothing."""
    return portal.ensure_built().live_cycle_now()


# --- capital sleeve (confidence ladder) ------------------------------------
@router.get("/sleeve")
def sleeve_get():
    """Cached sleeve ladder status (gate, recommended $, edge vs hold)."""
    import json
    from ..analytics.sleeve import STATUS_PATH
    if STATUS_PATH.exists():
        try:
            return json.loads(STATUS_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {
        "gate": "NO DATA", "n": 0, "pending": 0, "horizon": 5,
        "recommended_sleeve_usd": 0, "paper_shadow_usd": 0,
        "release_stage_hint": "backtest_only",
        "rewards_unlocked": [], "deep_dive_symbols": [],
        "edge_vs_hold_pp": None,
        "advice": "Run Refresh grade to score forward records.",
        "as_of": None,
    }


@router.post("/sleeve/refresh")
def sleeve_refresh():
    """Re-grade forward_record and persist data/sleeve_status.json."""
    from ..analytics.sleeve import evaluate_sleeve, save_status
    status = evaluate_sleeve()
    save_status(status)
    return status.to_dict()


# --- performance / autonomy ------------------------------------------------
@router.get("/performance")
def performance():
    """Book + sleeve scoreboard and current autonomy trade policy."""
    import json
    from ..analytics.performance import snapshot, OUT
    if OUT.exists():
        try:
            return json.loads(OUT.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return snapshot(portal.ensure_built())


@router.post("/autonomy/cycle")
def autonomy_cycle():
    """Run one earned-autonomy cycle: grade, plan, policy-gated exec, proposals."""
    return portal.ensure_built().run_autonomy_cycle()


@router.get("/autonomy/policy")
def autonomy_policy():
    from ..analytics.performance import snapshot
    return snapshot(portal.ensure_built())["policy"]


# --- campaign (mission to $1M) ---------------------------------------------
@router.get("/campaign")
def campaign():
    """Progress toward the target, pace, drawdown guardrail, and exit clock."""
    return portal.ensure_built().campaign_status()


# --- kill switch -----------------------------------------------------------
@router.post("/kill")
def kill():
    portal.ensure_built().executor.kill()
    return {"killed": True}


@router.post("/resume")
def resume():
    portal.ensure_built().executor.resume()
    return {"killed": False}


# --- loop control ----------------------------------------------------------
@router.post("/loop/start")
def loop_start():
    portal.start_loop()
    return {"loop_running": True}


@router.post("/loop/stop")
def loop_stop():
    portal.stop_loop()
    return {"loop_running": False}


# --- manual orders (confirm-mode approval + manual entry) ------------------
class OrderRequest(BaseModel):
    symbol: str
    side: str            # "buy" | "sell"
    quantity: float
    order_type: str = "market"
    limit_price: float | None = None


@router.post("/orders/manual")
def manual_order(req: OrderRequest):
    try:
        side = Side(req.side.lower())
        otype = OrderType(req.order_type.lower())
    except ValueError as exc:
        raise HTTPException(400, str(exc))

    portal.ensure_built()
    order = Order(symbol=req.symbol.upper(), side=side, quantity=req.quantity,
                  order_type=otype, limit_price=req.limit_price)
    q = portal.market.quote(order.symbol)
    broker = portal.executor._broker_for(order)
    account = broker.get_account()
    decision = portal.risk.approve(order, account, q.price)
    if not decision.approved:
        raise HTTPException(422, f"risk rejected: {decision.reason}")
    result = portal.executor._submit(order, broker)
    return portal._order_dict(result.order)


@router.post("/orders/{order_id}/approve")
def approve_pending(order_id: str):
    portal.ensure_built()
    q_price = 0.0
    order = next((o for o in portal.executor.pending if o.id == order_id), None)
    if order is not None:
        q_price = portal.market.quote(order.symbol).price
    res = portal.executor.approve_pending(order_id, q_price)
    return portal._order_dict(res.order)


@router.post("/orders/{order_id}/reject")
def reject_pending(order_id: str):
    ok = portal.ensure_built().executor.reject_pending(order_id)
    if not ok:
        raise HTTPException(404, "order not in pending queue")
    return {"cancelled": order_id}


# --- trade-style support --------------------------------------------------
# The dashboard was built around positions and orders. The operator does not
# decide in either unit: entries are THEMES with a structural backstop, exits
# are theme rotations. And the most expensive mistake in the ten-year record —
# ~$148k of forgone gains from three mass liquidations, against ~$70k of total
# realised profit — is invisible on a positions table by construction. A stock
# you no longer hold produces no row, no drawdown and no alert.

@router.get("/themes")
def themes():
    """Theme state and where the book is exposed — the operator's entry/exit unit."""
    from ..intel.themes import report as themes_report
    return themes_report()


@router.get("/exits")
def exits():
    """What selling cost, and which sold names sit in a still-live theme.

    Slow: it prices every past exit against current data. The dashboard should
    poll this on a long interval, not with the quote refresh.
    """
    from ..intel.exit_discipline import report as exit_report
    return exit_report()


@router.get("/intraday")
def intraday(symbols: str = ""):
    """Minute-bar coverage, per-session excursions, and send-timing.

    One call so the cockpit polls once. Deliberately reports its own sample
    size on every field: a send-window built on three sessions and one built on
    three hundred look identical once they reach a panel, and the operator has
    no way to tell them apart unless the number travels with them.
    """
    from ..analytics.execution import MIN_DAYS_FOR_RECOMMENDATION, recommend
    from ..analytics.intraday import daily_features
    from ..data.minute import coverage

    cov = coverage()
    want = [s.strip().upper() for s in symbols.split(",") if s.strip()] or sorted(cov)
    out = {}
    for s in want:
        feats = daily_features(s)
        rec = recommend(s)
        out[s] = {
            "sessions": len(feats),
            "coverage": cov.get(s),
            "recent": [
                {"date": f.date, "open": f.open, "close": f.close,
                 "mae_pct": f.mae_pct, "mfe_pct": f.mfe_pct,
                 "mae_minute": f.mae_minute, "mfe_minute": f.mfe_minute,
                 "range_pct": f.range_pct, "close_loc": f.close_loc}
                for f in feats[-10:]
            ],
            "send_window": rec.get("recommendation"),
            "timing_status": rec.get("status"),
            "timing_why": rec.get("why"),
        }
    return {
        "symbols": out,
        "min_sessions_for_timing": MIN_DAYS_FOR_RECOMMENDATION,
        "note": ("Minute bars are UNADJUSTED; daily bars in data/ohlc are adjusted. "
                 "Never compare the two across a split."),
    }


@router.get("/style")
def style():
    """Everything the trade style needs, in one call the dashboard can poll."""
    from ..intel.exit_discipline import report as exit_report
    from ..intel.themes import ALIVE, ROTATING_OUT, DEAD, report as themes_report
    t = themes_report()
    e = exit_report()
    live = [n for n, s in t["themes"].items() if s["state"] == ALIVE]
    rotating = [n for n, s in t["themes"].items()
                if s["state"] in (ROTATING_OUT, DEAD) and s["exposure_usd"] > 0]
    return {
        "themes": t["themes"],
        "benchmark_6m_pct": t["benchmark_6m_pct"],
        "live_themes": live,
        "exposed_to_rotating": rotating,
        "reentry_candidates": e["reentry_candidates"],
        "forgone_total": e["total_forgone_now"],
        "liquidation_days": e["liquidation_days"],
    }


@router.get("/board")
def board():
    """Flight status — GO/NO-GO per subsystem, ordered by consequence.

    Slow (it walks the trade history and prices past exits), so the dashboard
    polls this on a long interval. A status board that costs a page-load is a
    status board people stop opening.
    """
    from ..analytics.statusboard import board as sb
    return sb()


@router.get("/sectors")
def sectors():
    """Market-wide sector rotation — measured whether or not we hold it."""
    from ..analytics.sectors import report as sr
    return sr()


@router.get("/agents")
def agents():
    """Live agent state — what ran, what is due, what needs a human.

    Feeds the dashboard's agent panel. The point is to make the system's work
    VISIBLE: twelve research agents run on their own cadences and, until this
    existed, the only evidence was numbers quietly changing.
    """
    from ..agent.orchestrator import human_queue, what_is_due
    from ..agent.roster import ROSTER

    due = {d.agent.name: d for d in what_is_due()}
    p = portal.ensure_built()
    orch = p.orchestrator.status() if p.orchestrator else {}
    return {
        "orchestrator": {"running": orch.get("running", False),
                         "ticks": orch.get("ticks", 0),
                         "tick_seconds": orch.get("tick_seconds"),
                         "last_ran": orch.get("last_ran", [])},
        "agents": [
            {"name": a.name, "kind": a.kind, "leg": a.leg,
             "cadence": a.cadence_class, "owns": a.owns,
             "due": due[a.name].due if a.name in due else False,
             "reason": due[a.name].reason if a.name in due else "",
             "last_run": due[a.name].to_dict()["last_run"] if a.name in due else None,
             "wired": bool(a.entrypoint)}
            for a in ROSTER
        ],
        "human_queue": human_queue(),
    }


@router.get("/research")
def research():
    """The researcher's latest outward brief — shortlist, sentiment, MCP queue.

    Reads the persisted artefact rather than rebuilding: the orchestrator writes
    it on a daily cadence, and rebuilding on every dashboard poll would rescan
    the universe for a page refresh.
    """
    from ..analytics.researcher import latest
    return latest() or {"empty": True,
                        "note": "no brief yet — the researcher runs on a daily cadence"}


@router.get("/decisions")
def decisions():
    """The chief's decision queue — every checker's output, routed and gated.

    One artefact instead of eleven reports. Each record carries its evidence
    with provenance and a falsifier, so a decision read a week later can still
    be checked rather than merely remembered.
    """
    from ..agent.chief import build_queue
    return build_queue()
