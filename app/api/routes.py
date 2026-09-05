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


@router.post("/agent/schedule/start")
def agent_schedule_start():
    return portal.ensure_built().start_schedule()


@router.post("/agent/schedule/stop")
def agent_schedule_stop():
    return portal.ensure_built().stop_schedule()


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
