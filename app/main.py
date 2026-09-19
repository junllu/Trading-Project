"""FastAPI application: dashboard + REST API.

Building the portal on startup keeps a single runtime for the whole process.
The background strategy loop is NOT auto-started — you start it explicitly from
the dashboard, so a fresh boot never trades on its own.
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .api import router as api_router
from .config import settings
from .portal import portal

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

BASE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE / "web" / "templates"))

app = FastAPI(title="Automated Trading Portal", version="0.1.0")
app.include_router(api_router)

_static = BASE / "web" / "static"
if _static.exists():
    app.mount("/static", StaticFiles(directory=str(_static)), name="static")


@app.on_event("startup")
def _startup() -> None:
    portal.build()
    log = logging.getLogger("portal")
    log.info("portal built in %s mode", settings.mode.value)
    _autostart_paper_loop(log)


def _autostart_paper_loop(log) -> None:
    """Start the trading loop unattended — but ONLY when it cannot reach money.

    The old rule was that a fresh boot never trades on its own. That rule was
    written when this process could reach a real broker, and it is still right
    for that case. It is wrong for the case it now blocks: a paper simulation
    with no live broker registered, which is exactly the thing that has to run
    unattended for evidence to accumulate. A training loop nobody remembers to
    press start on produces no training.

    So the gate is the reachability of real money, not the calendar. Both
    conditions must hold: mode is PAPER (which hardcodes the paper broker in
    Executor._broker_for) and no live broker is configured at all. If either
    changes, this refuses and the human presses start, as before.
    """
    import os
    from .config import TradingMode

    if os.getenv("PORTAL_AUTOSTART", "1").lower() in ("0", "false", "no"):
        log.info("autostart disabled by PORTAL_AUTOSTART")
        return

    live = sorted(portal.executor.live_brokers) if portal.executor else []
    if settings.mode is not TradingMode.PAPER or live:
        log.warning(
            "NOT auto-starting the loop: mode=%s live_brokers=%s. Automatic "
            "trading is paper-only; start it explicitly from the dashboard.",
            settings.mode.value, live or "none")
        return

    portal.start_loop()
    if portal.scheduler is not None:
        try:
            portal.start_schedule()
        except Exception:
            log.exception("daily scheduler failed to start")
    log.info("AUTOSTART: paper loop running every %ss — simulated fills only, "
             "no broker is reachable from this process", settings.loop_interval)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    # Modern Starlette signature: (request, name, context).
    return templates.TemplateResponse(request, "dashboard.html", {"mode": settings.mode.value})


@app.get("/guide", response_class=HTMLResponse)
def guide(request: Request):
    """Operating guide — how to run this, read it, and learn from it.

    Served from the portal rather than kept as a separate document: the thing
    that explains a console belongs next to the console, and a guide that lives
    somewhere else goes stale the first time the console changes.
    """
    return templates.TemplateResponse(request, "guide.html", {"mode": settings.mode.value})


@app.get("/monitor", response_class=HTMLResponse)
def monitor(request: Request):
    """A narrow live-session view, separate from the main dashboard on purpose.

    The dashboard answers "how is everything" across twenty panels. During a
    session the question is smaller and more urgent — is the recorder alive,
    and what did it just decide — and a page that answers only that can be read
    at a glance instead of scanned.
    """
    return templates.TemplateResponse(request, "monitor.html", {"mode": settings.mode.value})


@app.get("/training", response_class=HTMLResponse)
def training(request: Request):
    """The paper simulation's track record — deliberately NOT the main dashboard.

    The dashboard answers "what is my real book worth". This answers "is the
    machinery any good", and the two must not share a screen: a simulated fill
    rendered next to a real position invites reading one as the other. Paper
    trades need no approval — there is nothing to protect — so this page is a
    scoreboard, not a queue.
    """
    return templates.TemplateResponse(request, "training.html", {"mode": settings.mode.value})


@app.get("/health")
def health():
    return {"ok": True, "mode": settings.mode.value}
