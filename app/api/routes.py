"""REST API for the dashboard and any external control.

Endpoints are intentionally small and JSON-first so the same API backs the web
UI, a future mobile client, or a CLI.
"""
from __future__ import annotations

import time

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


@router.get("/monitor")
def monitor(limit: int = 40):
    """Live-session monitor: what the agent is deciding, right now.

    Deliberately narrow. The main dashboard answers "how is everything"; this
    answers "is it running, and what did it just decide" during a session. It
    reads the forward record — the ARTEFACT — rather than the loop's in-memory
    state, so it stays truthful when the loop is down instead of going blank.

    The distinction that matters on this page: `intended_orders` are what the
    agent WOULD do. Nothing here has been executed — `executed` is on every row
    so a reader can never mistake a recorded intent for a fill.
    """
    import json as _json
    from ..agent.live_loop import RECORD_PATH, report as live_report
    from ..data.market_hours import state as session_state

    rows = []
    if RECORD_PATH.exists():
        for line in RECORD_PATH.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(_json.loads(line))
            except _json.JSONDecodeError:
                continue
    rows = rows[-limit:]

    live: dict = {}
    try:
        p = portal.ensure_built()
        live = p.live.status() if getattr(p, "live", None) else {}
    except Exception as exc:
        live = {"error": f"{type(exc).__name__}: {exc}"}

    latest = rows[-1] if rows else None
    return {
        "session": session_state().to_dict(),
        "live": live,
        "record": live_report(),
        "latest": latest,
        "history": [
            {"at": r.get("recorded_at_et"), "session": r.get("session"),
             "equity": r.get("equity"), "halted": r.get("halted"),
             "intents": len(r.get("intended_orders") or []),
             "top": (r.get("convictions") or [{}])[0].get("symbol"),
             "executed": r.get("executed", False)}
            for r in rows
        ],
        "note": ("intended_orders are INTENTS, never fills. Nothing on this page "
                 "has been executed — the executor is confirm-only and no order "
                 "reaches a broker without explicit approval."),
    }


@router.get("/doctrine")
def doctrine():
    """Structural theses graded against published series, not against price.

    Slow: it reads FRED (cached 12h on disk). Poll on a long interval — the
    fastest indicator here is a daily credit spread and most are monthly or
    quarterly, so anything more frequent is re-rendering the same numbers.
    """
    from ..macro.doctrine import report as doctrine_report
    from ..macro.signals import halving_projection
    out = doctrine_report()
    # The issuance clock rides along: it is macro CONTEXT on the same slow poll,
    # not a decision, so it belongs beside the doctrines rather than in the
    # attack/defend queue where every row implies something to do.
    try:
        out["cycle_clock"] = halving_projection()
    except Exception as exc:
        out["cycle_clock"] = {"error": f"{type(exc).__name__}: {exc}"}
    return out


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


@router.get("/training/account")
def training_account():
    """What the simulated account is actually WORTH, marked at live prices.

    The blotter answers "what was traded"; this answers "what is it worth now",
    which is the question fills and notional cannot. Realized P&L alone reads
    $0 here because the seeded holdings were sold without a matching buy in the
    record — so the bottom line has to include what is still open, marked to
    the live feed rather than to cost.
    """
    from ..engine import blotter
    from ..portfolio import sim_account

    p = portal.ensure_built()
    paper = p.brokers.get("paper")
    if paper is None:
        return {"available": False, "why": "no paper broker"}

    # Prime the live feed so the mark is current rather than whatever was last
    # quoted — a stale mark is the whole failure this page exists to avoid.
    syms = [pos.symbol for pos in paper.get_positions()]
    primer = getattr(p.quote_source, "prime", None)
    if callable(primer) and syms:
        try:
            primer(syms)
        except Exception:
            pass
    # prime() only warms the quote SOURCE. MarketData keeps its own `_last`
    # cache and its is_real() flags, and both are populated by quoting — so
    # without this refresh 28 of 29 positions read as unpriced and the account
    # totalled $1,714 against a book worth $134,000.
    if syms:
        p.market.refresh(syms)

    positions, invested, cost_total, unpriced = [], 0.0, 0.0, []
    # Inherited vs earned. Seeded names came with the book; anything else the
    # simulator chose to open. Averaging them answers neither question — the
    # unrealized on a seeded winner says nothing about whether the strategy is
    # any good, and burying the strategy's own positions inside it hides the
    # only number that does.
    seeded_set = set(sim_account.inception().get("seeded_symbols") or [])
    seeded_val = seeded_cost = acquired_val = acquired_cost = 0.0
    for pos in paper.get_positions():
        real = p.market.is_real(pos.symbol)
        q = p.market.last(pos.symbol)
        px = q.price if q else None
        if px is None or not real:
            unpriced.append(pos.symbol)
            continue
        value = pos.quantity * px
        cost = pos.quantity * pos.avg_price
        invested += value
        cost_total += cost
        is_seeded = pos.symbol in seeded_set
        if is_seeded:
            seeded_val += value
            seeded_cost += cost
        else:
            acquired_val += value
            acquired_cost += cost
        pct = (px / pos.avg_price - 1) * 100 if pos.avg_price else None

        # PLAN vs REALITY. The plan was written at entry; this is where the
        # position actually sits against it. Without the comparison the plan is
        # decoration — a stop nobody checks and targets nobody scores.
        plan = None
        try:
            from ..agent import position_plans
            rec = position_plans.get(pos.symbol)
        except Exception:
            rec = None
        if rec:
            stop_price = rec.get("stop_price")
            targets = [t.get("gain_pct") for t in (rec.get("targets") or [])]
            hit = [g for g in targets if pct is not None and pct >= g]
            room = ((px / stop_price - 1) * 100
                    if stop_price and px and stop_price > 0 else None)
            plan = {
                "entry_price": rec.get("entry_price"),
                "stop_pct": rec.get("stop_pct"),
                "stop_price": stop_price,
                "stop_basis": rec.get("stop_basis"),
                "targets_pct": targets,
                "targets_hit": hit,
                "next_target_pct": next((g for g in targets
                                         if pct is None or pct < g), None),
                "room_to_stop_pct": round(room, 2) if room is not None else None,
                # Drifting below the recorded entry is not the same as being
                # underwater against an average that later adds moved.
                "vs_entry_pct": (round((px / rec["entry_price"] - 1) * 100, 2)
                                 if rec.get("entry_price") else None),
                "status": ("STOP BREACHED" if stop_price and px <= stop_price
                           else ("TARGET " + str(max(hit)) + "%" if hit else "in plan")),
            }

        positions.append({
            "symbol": pos.symbol, "quantity": round(pos.quantity, 4),
            "avg_price": round(pos.avg_price, 4), "last": round(px, 4),
            "value": round(value, 2),
            "unrealized": round(value - cost, 2),
            "unrealized_pct": round(pct, 2) if pct is not None else None,
            "source": (p.quote_source.provenance(pos.symbol) or {}).get("source"),
            "plan": plan,
            "origin": "seeded" if is_seeded else "sim",
        })

    realized = blotter.realized()
    positions.sort(key=lambda x: -abs(x["unrealized"]))

    total_value = paper.cash + invested
    start = sim_account.inception()
    start_value = float(start.get("value") or 0.0)
    pnl = total_value - start_value if start_value else None

    return {
        "available": True,
        "as_of": time.strftime("%Y-%m-%d %H:%M:%S"),
        "inception": start,
        "pnl_from_inception": round(pnl, 2) if pnl is not None else None,
        "pnl_from_inception_pct": (round(pnl / start_value * 100, 2)
                                   if pnl is not None and start_value else None),
        "cash": round(paper.cash, 2),
        "positions_value": round(invested, 2),
        "total_value": round(paper.cash + invested, 2),
        "cost_basis": round(cost_total, 2),
        "unrealized": round(invested - cost_total, 2),
        "realized_closed": realized.get("realized_usd"),
        "round_trips": realized.get("round_trips"),
        "open_positions": len(positions),
        # The split that makes the headline number readable. Strategy P&L is
        # the `acquired` line; `seeded` is the book's own drift, which the
        # simulator neither caused nor can be judged on.
        "seeded": {
            "value": round(seeded_val, 2), "cost": round(seeded_cost, 2),
            "unrealized": round(seeded_val - seeded_cost, 2),
            "count": sum(1 for x in positions if x["origin"] == "seeded"),
        },
        "acquired": {
            "value": round(acquired_val, 2), "cost": round(acquired_cost, 2),
            "unrealized": round(acquired_val - acquired_cost, 2),
            "count": sum(1 for x in positions if x["origin"] == "sim"),
        },
        "positions": positions[:40],
        "unpriced": unpriced,
        "sim_account": sim_account.summary(),
        "note": ("unrealized is marked to the LIVE feed; realized counts only "
                 "round trips opened and closed inside this record"),
    }


@router.get("/heartbeat")
def heartbeat():
    """Liveness per component — is anything silently dead?

    Every other page renders happily on stale data, so an unattended job that
    stops looks identical to one that is working. This is the only surface that
    answers "did it actually run", cadence-relative and market-aware.
    """
    from ..agent.heartbeat import scan
    return scan()


@router.get("/blotter")
def blotter(limit: int = 100):
    """The durable fill record — what the simulated book actually did.

    Distinct from `recent_orders` in /api/status, which is this process's
    in-memory list and dies with the server. This survives restarts, which is
    what makes a paper run evidence rather than a status light.
    """
    from ..engine import blotter as bl
    return {"summary": bl.summary(), "rows": bl.rows()[-limit:][::-1]}


@router.get("/decisions")
def decisions():
    """The chief's decision queue — every checker's output, routed and gated.

    One artefact instead of eleven reports. Each record carries its evidence
    with provenance and a falsifier, so a decision read a week later can still
    be checked rather than merely remembered.
    """
    from ..agent.chief import build_queue
    return build_queue()
