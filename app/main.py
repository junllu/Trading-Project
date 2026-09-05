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
    logging.getLogger("portal").info("portal built in %s mode", settings.mode.value)


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    # Modern Starlette signature: (request, name, context).
    return templates.TemplateResponse(request, "dashboard.html", {"mode": settings.mode.value})


@app.get("/health")
def health():
    return {"ok": True, "mode": settings.mode.value}
