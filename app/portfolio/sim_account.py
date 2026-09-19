"""The simulated book, persisted — so tomorrow continues from tonight.

WHAT WAS WRONG

`portal.build()` re-seeded the paper broker from `holdings.yaml` on every
process start. So the simulation had no memory: a day of trading changed the
in-memory positions, the process ended, and the next run began again from the
real book as though nothing had happened. Positions bought in the simulation
never existed the following morning, and a trimmed position came back whole.

That makes multi-day evidence impossible. A swing strategy holding four days
cannot be measured by a book that resets every few hours, and the exit monitor
would grade positions it had already sold.

WHAT THIS DOES

Persists the simulated account — positions, cost basis and cash — to
`data/sim_account.json`, and restores it on the next start. The real
`holdings.yaml` becomes the SEED for day one only; after that the simulation
owns its own book and diverges from the real one, which is the entire point.
Two books that must diverge cannot share one file.

WHY COST BASIS IS CARRIED, NOT RECOMPUTED

Expectancy is measured per closed trade against the price actually paid. If a
restart reset cost basis to the real book's average, every simulated trade would
be scored against a purchase that did not happen in the simulation, and the win
and loss sizes — the numbers expectancy is built from — would be fiction.

RESETTING IS DELIBERATE AND EXPLICIT

`reset()` re-seeds from the real book and archives what it replaced. It is never
automatic: silently discarding an accumulating track record because a file
looked stale is exactly the failure that makes a simulation untrustworthy.
"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime
from typing import Any

from ..config import ROOT
from ..models import Position

PATH = ROOT / "data" / "sim_account.json"
ARCHIVE_DIR = ROOT / "data" / "sim_archive"

# Every artefact the TRAINING run produces, named in one place.
#
# Live trading will eventually write its own records, and the two must never be
# read as one series. A simulated fill and a real fill look identical in a
# blotter row — same schema, same fields — and once mixed there is no way to
# separate them after the fact. So the separation is by FILE, decided before
# live exists rather than retrofitted after the first confusing report.
TRAINING_ARTEFACTS = (
    "sim_account.json",       # the simulated book
    "paper_blotter.jsonl",    # simulated fills
    "exit_state.json",        # which rungs the sim has spent
    "sim_archive",            # superseded simulated books
)


def is_training_artefact(path) -> bool:
    """Does this file belong to the training run rather than the real one?"""
    name = str(path).replace("\\", "/").rsplit("/", 1)[-1]
    return name in TRAINING_ARTEFACTS


def exists() -> bool:
    return PATH.exists()


def inception() -> dict[str, Any]:
    """What the simulated account was worth when it started.

    Without this, P&L has no origin: the blotter shows realized round trips and
    the positions show unrealized against cost, but neither answers "is the
    account up or down since we began". Every other number here is a delta from
    something, and this is the something.

    Stored once, on the first save that lacks it, and never rewritten — an
    origin that moves is not an origin. Where the running record predates this
    field it is reconstructed from the seed (holdings.yaml shares x their
    last-synced price, plus cash) and flagged `reconstructed`, because a
    baseline inferred after the fact should not be presented as one that was
    measured at the time.
    """
    if PATH.exists():
        try:
            d = json.loads(PATH.read_text(encoding="utf-8"))
            inc = d.get("inception")
            # Backfilled, not ignored. Records written before `seeded_symbols`
            # existed have no seed list, and an EMPTY list is indistinguishable
            # from "nothing was seeded" — which classified all 25 inherited
            # positions as sim-opened and credited the strategy with the entire
            # book it was handed. A missing field means unknown, never none.
            if inc and not inc.get("seeded_symbols"):
                try:
                    from .holdings import load_holdings
                    inc["seeded_symbols"] = sorted(
                        {str(h["symbol"]).upper() for h in load_holdings()})
                    inc["seeded_backfilled"] = True
                except Exception:
                    pass
            if inc:
                return inc
        except (OSError, json.JSONDecodeError):
            pass

    from .holdings import load_cash, load_holdings
    total, cash = 0.0, 0.0
    seeded: list[str] = []
    try:
        for h in load_holdings():
            shares = float(h.get("shares") or 0)
            px = float(h.get("last") or h.get("avg_price") or 0)
            total += shares * px
            seeded.append(str(h["symbol"]).upper())
        cash = float(load_cash() or 0.0)
    except Exception:
        pass
    return {
        "value": round(total + cash, 2),
        "positions_value": round(total, 2),
        "cash": round(cash, 2),
        # Which names the account STARTED with. Everything else was opened by a
        # decision the simulator made, and the two must be scored apart: a
        # seeded position's gain was inherited, not earned, and folding it into
        # "the simulated account's P&L" credits the strategy with the book it
        # was handed.
        "seeded_symbols": sorted(set(seeded)),
        "as_of": datetime.now().strftime("%Y-%m-%d"),
        "reconstructed": True,
        "basis": "holdings.yaml shares x last-synced price + cash",
    }


def save(broker, note: str = "") -> dict[str, Any]:
    """Snapshot the simulated account. Called on tick and at session end."""
    positions = [
        {"symbol": p.symbol, "quantity": round(float(p.quantity), 6),
         "avg_price": round(float(p.avg_price), 6)}
        for p in broker.get_positions()
    ]
    prices = {}
    try:
        prices = {s: round(float(v), 6) for s, v in getattr(broker, "_prices", {}).items()}
    except Exception:
        prices = {}

    payload = {
        "as_of": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "session_date": datetime.now().strftime("%Y-%m-%d"),
        "ts": time.time(),
        "cash": round(float(broker.cash), 6),
        "positions": positions,
        "last_prices": prices,
        "note": note,
        "schema": 1,
    }
    # Written once and then carried forward untouched. An origin that moves
    # would make every P&L figure derived from it meaningless.
    try:
        existing = json.loads(PATH.read_text(encoding="utf-8")) if PATH.exists() else {}
    except (OSError, json.JSONDecodeError):
        existing = {}
    payload["inception"] = existing.get("inception") or inception()
    try:
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(PATH)                     # atomic: a killed run cannot truncate it
    except OSError as exc:
        payload["_write_error"] = str(exc)
    return payload


def load(broker) -> dict[str, Any] | None:
    """Restore a saved simulated account onto a fresh paper broker."""
    if not PATH.exists():
        return None
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

    broker._positions.clear()
    for p in d.get("positions") or []:
        try:
            sym = str(p["symbol"]).upper()
            broker._positions[sym] = Position(
                symbol=sym, quantity=float(p["quantity"]),
                avg_price=float(p["avg_price"]), broker=broker.name)
        except (KeyError, TypeError, ValueError):
            continue
    for sym, px in (d.get("last_prices") or {}).items():
        try:
            broker.set_price(str(sym).upper(), float(px))
        except (TypeError, ValueError):
            continue
    try:
        broker.cash = float(d.get("cash", broker.cash))
    except (TypeError, ValueError):
        pass
    return d


def reset(broker, holdings: list[dict] | None = None) -> dict[str, Any]:
    """Archive the current simulated book and re-seed from the real one.

    Explicit by design. An accumulating track record is the asset here, so
    replacing it is an action someone takes on purpose, and the thing replaced
    is kept rather than deleted.
    """
    archived = None
    if PATH.exists():
        try:
            ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
            archived = ARCHIVE_DIR / f"sim_account_{datetime.now():%Y%m%d_%H%M%S}.json"
            shutil.copy2(PATH, archived)
        except OSError:
            archived = None

    from .holdings import load_cash, load_holdings, seed_paper_broker
    broker._positions.clear()
    hs = holdings if holdings is not None else load_holdings()
    seeded = seed_paper_broker(broker, hs) if hs else []
    cash = load_cash()
    broker.cash = float(cash) if cash is not None else 0.0
    out = save(broker, note="reset from holdings.yaml")
    out["seeded_symbols"] = seeded
    out["archived_to"] = str(archived) if archived else None
    return out


def summary() -> dict[str, Any]:
    if not PATH.exists():
        return {"exists": False,
                "note": "no simulated account yet — it seeds from holdings.yaml "
                        "on first run and persists from then on"}
    try:
        d = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"exists": True, "error": str(exc)}
    return {
        "exists": True, "as_of": d.get("as_of"),
        "session_date": d.get("session_date"),
        "cash": d.get("cash"),
        "positions": len(d.get("positions") or []),
        "path": str(PATH),
    }
