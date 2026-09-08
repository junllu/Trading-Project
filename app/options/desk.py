"""The OPTIONS leg — an INDEPENDENT pool, not a view derived from the core's.

This sleeve trades volatility around scheduled events. The core pool holds
theses to 2028. They are separate books with separate edges, and the only thing
they share is the underlying shares — so that is the only place they talk.

    INDEPENDENT   event calendar + realised vol + vol rank + liquidity.
                  No fundamental input at all.
    COORDINATED   covered calls and cash-secured puts ONLY, because both can
                  hand the CORE pool a share position it did not choose.

An earlier version got this wrong and it is worth recording why, because the
mistake is seductive. It fed the core leg's operating character into the
structure decision, reasoning that "MU's revenue growth is 97% price" should
tell the options desk to sell premium. Two things were broken by that:

  1. It made this sleeve a DERIVATIVE of the core research. A wrong fundamental
     read would then break both pools simultaneously — precisely the correlated
     failure that having two pools is supposed to prevent.
  2. It refused good setups whenever the core layer simply had no data. ADBE was
     rejected at realised vol 0.51 with earnings three days out, for no reason
     other than that nobody had pulled its 10-Q.

Volatility is now priced against the name's own history instead. `vol_rank`
answers "is premium expensive for THIS name, by its own standards" without
consulting a single fundamental. Rich vol sells, cheap vol with a catalyst buys,
mid-range does nothing.

The gates, in order:

  1. Calendar. No scheduled catalyst inside the horizon -> no trade. Theta is
     certain and a thesis is not a catalyst. Most weeks the answer is nothing.
  2. Vol rank. Rich (>=70) sells premium, cheap (<=30) buys convexity, and the
     middle has no edge. An absolute floor still applies: cheap options on a
     name that does not move are worthless.
  3. Coordination — for covered calls and cash-secured puts only. A live core
     thesis vetoes writing calls on its shares (assignment is the "sold the
     winner early" mistake automated), and a put is only sold on a name the
     core pool would genuinely own at the strike.

Honest limits, stated rather than buried: realised vol is a PROXY for implied.
Without option chains there is no true IV rank. The error has a known direction
— after a shock, realised rank UNDERSTATES how rich premium has become — so
this reads conservative exactly when the sleeve is most tempted. Every output is
a candidate for review. Nothing here places, prices, or sizes an order, and the
sleeve is NOT_FUNDED.

    python -m app.options.desk
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime

from ..config import ROOT

EVENTS_DIR = ROOT / "data" / "options"
PRICES_DIR = ROOT / "data" / "prices"

HORIZON_DAYS = 45          # 30-60 DTE band from config/config.yaml
MIN_VOL_FOR_CONVEXITY = 0.45
MAX_PER_POSITION = 1500.0
EVENT_NEAR_DAYS = 7        # inside this, an event dominates the premium

# Volatility percentiles, not fundamentals, decide direction.
RICH_RANK = 70.0
CHEAP_RANK = 30.0

COVERED_CALL = "COVERED_CALL"
CASH_SECURED_PUT = "CASH_SECURED_PUT"
CREDIT_SPREAD = "CREDIT_SPREAD"
DEFINED_RISK_DIRECTIONAL = "DEFINED_RISK_DIRECTIONAL"
NO_TRADE = "NO_TRADE"
BLOCKED = "BLOCKED_BY_CORE"

# The two structures whose assignment would hand the CORE pool a share
# position. Only these consult the thesis ledger; everything else is the
# options sleeve's own business and risks only its own premium.
COORDINATED_STRUCTURES = {COVERED_CALL, CASH_SECURED_PUT}


@dataclass
class Candidate:
    symbol: str
    verdict: str
    structure: str
    event_date: str | None
    days_to_event: int | None
    realized_vol: float | None
    vol_rank: float | None
    coordination: str            # "independent" or how the core pool ruled
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return dict(self.__dict__)


def _closes(symbol: str) -> list[float]:
    p = PRICES_DIR / f"{symbol.upper()}.csv"
    if not p.exists():
        return []
    out = []
    with p.open("r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                out.append(float(row["close"]))
            except (KeyError, ValueError, TypeError):
                continue
    return out


def _vol_of(rets: list[float]) -> float | None:
    if len(rets) < 2:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    return math.sqrt(var) * math.sqrt(252)


def realized_vol(symbol: str, window: int = 60) -> float | None:
    """Annualised realised vol over the recent window — the IV level proxy."""
    c = _closes(symbol)[-(window + 1):]
    if len(c) < 30:
        return None
    rets = [c[i] / c[i - 1] - 1 for i in range(1, len(c)) if c[i - 1]]
    v = _vol_of(rets)
    return round(v, 3) if v is not None else None


def vol_rank(symbol: str, window: int = 30, lookback: int = 252) -> float | None:
    """Where today's realised vol sits in its OWN trailing distribution, 0-100.

    This is the options leg's native input, and the reason it no longer borrows
    the core leg's fundamentals. A vol LEVEL says nothing on its own — 0.98 is
    calm for one name and extreme for another. A vol RANK is self-normalising:
    "this name's volatility is at the 85th percentile of its own year" is a
    statement about whether premium is expensive, which is the only question
    this sleeve actually needs answered.

    It is still a proxy. Real IV rank needs option chains; realised vol is
    backward-looking and will lag a genuine repricing. It is honest about the
    direction of that error: after a shock, realised rank UNDERSTATES how rich
    premium has become, so this reads conservative on exactly the days the
    sleeve is most tempted to act.
    """
    c = _closes(symbol)
    if len(c) < window + lookback // 4:
        return None
    rets = [c[i] / c[i - 1] - 1 for i in range(1, len(c)) if c[i - 1]]
    series = [v for i in range(window, len(rets) + 1)
              if (v := _vol_of(rets[i - window:i])) is not None]
    series = series[-lookback:]
    if len(series) < 40:
        return None
    current = series[-1]
    below = sum(1 for v in series if v < current)
    return round(100 * below / len(series), 1)


def latest_events() -> tuple[dict, str] | tuple[None, None]:
    files = sorted(EVENTS_DIR.glob("events_*.json")) if EVENTS_DIR.exists() else []
    if not files:
        return None, None
    return json.loads(files[-1].read_text(encoding="utf-8")), files[-1].stem.replace("events_", "")


def _core_verdicts() -> dict[str, str]:
    """The core pool's thesis verdicts — consulted ONLY by coordinate().

    There is deliberately no equivalent helper for the filings layer's
    operating character. An earlier version had one and fed it into the
    structure decision, which made this sleeve a derivative of the core
    research instead of an independent pool. If a future change needs
    fundamentals to pick an options structure, that is the bug returning.
    """
    try:
        from ..intel.thesis import report as thesis_report
        rep = thesis_report()
        return {r["symbol"]: r["verdict"] for r in rep.get("theses", [])}
    except Exception:
        return {}


def structure_for(vol: float | None, rank: float | None, days: int | None,
                  held_lots: int = 0) -> tuple[str, str, list[str]]:
    """The options leg's OWN decision — event and volatility only.

    Deliberately blind to fundamentals. An earlier version keyed this on the
    core leg's operating character, which was wrong twice over: it made the
    sleeve a derivative of the core research (so a bad fundamental read broke
    BOTH pools at once, defeating the point of having two), and it refused
    perfectly good setups whenever the core layer simply had no filing for the
    name. ADBE was blocked at vol 0.51 with earnings three days out purely
    because no 10-Q had been pulled for it.

    Volatility is priced by comparing a name to ITSELF, not to a thesis:

        rank >= 70   premium expensive vs its own year -> SELL it
        rank <= 30   premium cheap and a catalyst is due -> BUY convexity
        otherwise    neither rich nor cheap -> no edge, no trade

    Where the two pools touch the same underlying — a covered call written on
    shares the core pool owns, or a put whose assignment would hand the core
    pool a position — this returns a structure FLAGGED for coordination, and
    `coordinate()` applies the core leg's veto. That is the only coupling.
    """
    reasons: list[str] = []
    if days is None:
        return NO_TRADE, "-", ["no scheduled catalyst inside the horizon; "
                               "theta is certain, a thesis is not"]
    if vol is None:
        return NO_TRADE, "-", ["no price history cached — cannot price premium at all"]
    if rank is None:
        return NO_TRADE, "-", [f"realised vol {vol:.2f} known, but too little history to "
                               f"rank it — a level without a distribution is not a signal"]

    if rank >= RICH_RANK:
        reasons.append(f"realised vol {vol:.2f} ranks {rank:.0f}/100 against its own year — "
                       f"premium is expensive relative to this name's normal")
        if held_lots >= 1:
            reasons.append(f"{held_lots} lot(s) held, so a covered call is available — "
                           f"COORDINATION REQUIRED, the core pool owns these shares")
            return COVERED_CALL, f"covered call, {held_lots} lot(s) max, 30-45 DTE", reasons
        reasons.append("not held, so no assignment risk to the core pool — a defined-risk "
                       "credit spread stands alone")
        return CREDIT_SPREAD, "defined-risk credit spread; max loss = width - credit", reasons

    if rank <= CHEAP_RANK:
        if vol < MIN_VOL_FOR_CONVEXITY:
            reasons.append(f"vol is cheap at the rank {rank:.0f}/100, but the absolute "
                           f"level {vol:.2f} is under the {MIN_VOL_FOR_CONVEXITY} floor — "
                           f"cheap options on a name that does not move are still worthless")
            return NO_TRADE, "-", reasons
        reasons.append(f"realised vol {vol:.2f} at the rank {rank:.0f}/100 with a catalyst "
                       f"in {days}d — convexity is cheap relative to this name's own history")
        return DEFINED_RISK_DIRECTIONAL, "debit spread — capped loss, short leg funds the theta", reasons

    return NO_TRADE, "-", [f"vol rank {rank:.0f} is mid-range — premium is neither rich nor "
                           f"cheap, so there is no volatility edge to trade"]


def coordinate(symbol: str, verdict: str, core_verdict: str | None,
               reasons: list[str]) -> tuple[str, str, list[str]]:
    """The ONLY place the two pools touch: covered calls and cash-secured puts.

    Both hand the core pool a share position it did not choose — a covered call
    can have its shares called away, a short put can be assigned. Every other
    structure here risks only the sleeve's own premium and needs no permission.
    """
    if verdict not in COORDINATED_STRUCTURES:
        return verdict, "independent — this structure never touches core shares", reasons

    if verdict == COVERED_CALL:
        if core_verdict in {"INTACT", "WATCH"}:
            reasons.insert(0, f"core thesis is {core_verdict} on shares the core pool holds — "
                              f"assignment would sell the thesis for premium, which is the "
                              f"'sold the winner early' mistake automated")
            return BLOCKED, "core pool vetoed", reasons
        reasons.insert(0, f"core verdict {core_verdict or 'none'} — no live thesis to protect, "
                          f"so assignment is an acceptable outcome")
        return verdict, "cleared by the core pool", reasons

    # cash-secured put: assignment must be a position the core pool WANTS
    if core_verdict == "INTACT":
        reasons.insert(0, "core thesis is INTACT, so assignment hands the core pool a name "
                          "it already wants — getting paid to wait for an entry")
        return verdict, "cleared by the core pool", reasons
    reasons.insert(0, f"core verdict is {core_verdict or 'none'} — never sell a put on a name "
                      f"the core pool would not willingly own at the strike")
    return BLOCKED, "core pool vetoed", reasons


def build(today: date | None = None) -> dict:
    today = today or date.today()
    events, snap_date = latest_events()
    if events is None:
        return {"error": "no events snapshot in data/options/", "candidates": []}

    out: list[Candidate] = []
    # Resolved lazily, and ONLY to coordinate a covered call or a cash-secured
    # put. Keeping it off the main path is the point: the sleeve must still
    # reach a verdict when the core layer has no data on a name at all.
    core: dict[str, str] | None = None

    for ev in events["events"]:
        sym = ev["symbol"]
        d = datetime.strptime(ev["date"], "%Y-%m-%d").date()
        dte = (d - today).days
        if dte < 0 or dte > HORIZON_DAYS:
            continue

        vol, rank = realized_vol(sym), vol_rank(sym)
        verdict, structure, reasons = structure_for(vol, rank, dte, _held_lots(sym))

        coordination = "independent"
        if verdict in COORDINATED_STRUCTURES:
            if core is None:
                core = _core_verdicts()
            verdict, coordination, reasons = coordinate(sym, verdict, core.get(sym), reasons)

        if not ev.get("verified", True):
            reasons.append("date is UNCONFIRMED by the filer — tentative, do not size to it")
        out.append(Candidate(sym, verdict, structure, ev["date"], dte, vol, rank,
                             coordination, reasons))

    order = {DEFINED_RISK_DIRECTIONAL: 0, CREDIT_SPREAD: 1, COVERED_CALL: 2,
             CASH_SECURED_PUT: 3, BLOCKED: 4, NO_TRADE: 5}
    out.sort(key=lambda c: (order.get(c.verdict, 9), c.days_to_event or 999))
    return {"snapshot_date": snap_date, "as_of": today.isoformat(),
            "candidates": out, "absent": events.get("absent", {})}


def _held_lots(symbol: str) -> int:
    """Whole 100-share lots, per broker — lots do NOT merge across accounts.

    You cannot write one contract against 60 shares at Robinhood plus 60 at
    Webull; each account is covered separately or the call is naked there.
    """
    try:
        from ..portfolio.holdings import load_holdings
        by_broker: dict[str, float] = {}
        for h in load_holdings():
            if str(h["symbol"]).upper() == symbol.upper():
                b = str(h.get("broker", "?"))
                by_broker[b] = by_broker.get(b, 0.0) + float(h["shares"])
        return int(sum(v // 100 for v in by_broker.values()))
    except Exception:
        return 0


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rep = build()
    if "error" in rep:
        print(rep["error"])
        return
    if args.json:
        print(json.dumps({"snapshot_date": rep["snapshot_date"], "as_of": rep["as_of"],
                          "candidates": [c.to_dict() for c in rep["candidates"]]}, indent=2))
        return

    print("=" * 78)
    print("  OPTIONS DESK — event-driven, 30-60 DTE   [sleeve status: NOT_FUNDED]")
    print("=" * 78)
    print(f"  events {rep['snapshot_date']}  ·  as of {rep['as_of']}  ·  "
          f"horizon {HORIZON_DAYS}d\n")

    if not rep["candidates"]:
        print("  No catalyst inside the horizon. No trade is the correct output.")
    for c in rep["candidates"]:
        print(f"  {'-' * 74}")
        print(f"  {c.symbol:6} {c.verdict:26} {c.event_date}  (T-{c.days_to_event}d)")
        vol = f"{c.realized_vol:.2f}" if c.realized_vol else "n/a"
        rank = f"vol rank {c.vol_rank:.0f}/100" if c.vol_rank is not None else "unrankable"
        print(f"         realised vol {vol}  ·  {rank}  ·  {c.coordination}")
        if c.structure != "-":
            print(f"         -> {c.structure}")
        for r in c.reasons:
            print(f"         ·  {r}")

    ab = rep.get("absent") or {}
    if ab:
        print(f"\n  {'=' * 74}")
        print(f"  {ab.get('note', '')}")
        print(f"  {ab.get('implication', '')}")
    print("\n  Realised vol is a PROXY for implied. Without option chains there is no IV")
    print("  rank, so nothing here should be read as 'premium is objectively rich'.")


if __name__ == "__main__":
    _main()
