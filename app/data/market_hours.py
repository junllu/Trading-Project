"""US equity market session clock.

Answers "is the market open right now, and when does it next open or close" so
the live runner fires on real session boundaries instead of wall-clock guesses.

Deliberately dependency-free (no pandas_market_calendars): regular hours,
weekends, and the NYSE/Nasdaq holiday list, which is all the daily-bar strategy
needs. If you move to intraday execution, replace this with an exchange calendar
that also models early closes and ad-hoc halts.

Times are US/Eastern. Uses zoneinfo (stdlib) so DST is handled correctly.

    python -m app.data.market_hours
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:                                        # pragma: no cover
    ET = None

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)
PREMARKET_OPEN = time(4, 0)
AFTERHOURS_CLOSE = time(20, 0)

# NYSE/Nasdaq full closures. Extend as years roll forward.
HOLIDAYS: set[str] = {
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
    # 2027
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31",
    "2027-06-18", "2027-07-05", "2027-09-06", "2027-11-25", "2027-12-24",
}

# Early closes (1:00 pm ET).
HALF_DAYS: set[str] = {"2026-11-27", "2026-12-24", "2027-11-26"}


@dataclass
class SessionState:
    now_et: datetime
    is_trading_day: bool
    session: str            # "closed" | "premarket" | "regular" | "afterhours"
    next_open: datetime | None
    next_close: datetime | None

    @property
    def is_open(self) -> bool:
        return self.session == "regular"

    def to_dict(self) -> dict:
        return {"now_et": self.now_et.isoformat(timespec="seconds"),
                "is_trading_day": self.is_trading_day, "session": self.session,
                "is_open": self.is_open,
                "next_open": self.next_open.isoformat(timespec="seconds") if self.next_open else None,
                "next_close": self.next_close.isoformat(timespec="seconds") if self.next_close else None}


def _now_et() -> datetime:
    return datetime.now(ET) if ET else datetime.now()


def is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d.isoformat() not in HOLIDAYS


def close_time_for(d: date) -> time:
    return time(13, 0) if d.isoformat() in HALF_DAYS else REGULAR_CLOSE


def next_trading_day(d: date) -> date:
    nxt = d + timedelta(days=1)
    while not is_trading_day(nxt):
        nxt += timedelta(days=1)
    return nxt


def state(now: datetime | None = None) -> SessionState:
    now = now or _now_et()
    d = now.date()
    trading = is_trading_day(d)
    close_t = close_time_for(d)

    def at(day: date, t: time) -> datetime:
        naive = datetime.combine(day, t)
        return naive.replace(tzinfo=ET) if ET else naive

    session = "closed"
    if trading:
        if PREMARKET_OPEN <= now.time() < REGULAR_OPEN:
            session = "premarket"
        elif REGULAR_OPEN <= now.time() < close_t:
            session = "regular"
        elif close_t <= now.time() < AFTERHOURS_CLOSE:
            session = "afterhours"

    if trading and now.time() < REGULAR_OPEN:
        nxt_open = at(d, REGULAR_OPEN)
    else:
        nxt_open = at(next_trading_day(d), REGULAR_OPEN)

    if trading and now.time() < close_t:
        nxt_close = at(d, close_t)
    else:
        nd = next_trading_day(d)
        nxt_close = at(nd, close_time_for(nd))

    return SessionState(now_et=now, is_trading_day=trading, session=session,
                        next_open=nxt_open, next_close=nxt_close)


def seconds_until(dt: datetime, now: datetime | None = None) -> float:
    return max(0.0, (dt - (now or _now_et())).total_seconds())


if __name__ == "__main__":
    s = state()
    print(f"now (ET)      : {s.now_et:%Y-%m-%d %H:%M:%S %Z}")
    print(f"trading day   : {s.is_trading_day}")
    print(f"session       : {s.session}  (open={s.is_open})")
    print(f"next open     : {s.next_open:%Y-%m-%d %H:%M %Z}  "
          f"(in {seconds_until(s.next_open) / 3600:.1f}h)")
    print(f"next close    : {s.next_close:%Y-%m-%d %H:%M %Z}  "
          f"(in {seconds_until(s.next_close) / 3600:.1f}h)")
