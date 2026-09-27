"""Minute bars — a separate store, and a fetcher the portal cannot run itself.

WHY THIS IS SPLIT IN TWO

Daily OHLC (app/data/ohlc.py) comes from yfinance: the portal calls it directly,
on a schedule, with no credential worth guarding. Minute bars do not work that
way. Webull gates minute data behind an OpenAPI market-data subscription, and
the account key in .env does NOT have it — the local `webull` MCP server answers
every market-data call with:

    Market data requires quotes subscription. (HTTP 403)

The Anthropic-hosted `claude_ai_Webull` connector DOES have the subscription.
That connector is an MCP tool, so only Claude-in-session can call it; a Python
process cannot. Hence the split enforced by this module:

    Claude fetches   ->   ingest() normalizes, validates and merges
    Python owns      ->   the store, gap detection, and the fetch PLAN

`plan()` emits the exact MCP call arguments needed to close the gaps, so the
fetch loop is mechanical rather than improvised. If the subscription is ever
bought for the .env key, add a direct fetcher behind ingest() and nothing
downstream changes.

SHAPE OF THE DATA

    data/minute/TSLA.csv   ts,open,high,low,close,volume,session

`ts` is UTC ISO-8601 and marks the bar's START (a 19:59Z bar is the last RTH
minute; the close auction lands in it, which is why its volume dwarfs its
neighbours'). Sessions are PRE / RTH / ATH / OVN, kept as a column rather than
filtered on write — an execution-timing study wants RTH only, but a gap study
needs the overnight tape, and re-fetching to get it back is expensive.

NOT SPLIT-ADJUSTED. Webull returns the raw print. Daily bars in ohlc.py ARE
adjusted, so never compare a minute close to a daily close across a split
boundary. `splits_suspected()` flags the discontinuities rather than silently
papering over them.

    python -m app.data.minute --status
    python -m app.data.minute --plan MRVL --days 30
    python -m app.data.minute --ingest MRVL bars.json
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ..config import ROOT

MINUTE_DIR = ROOT / "data" / "minute"

# Webull's per-request ceiling for M1. Every other timespan caps at 1200; M1 is
# the exception. Requesting more silently truncates, which would look like a
# data gap and trigger an endless re-fetch of the same window.
MAX_BARS_PER_REQUEST = 1650

SESSIONS = ("PRE", "RTH", "ATH", "OVN")
RTH_MINUTES_PER_DAY = 390

FIELDS = ["ts", "open", "high", "low", "close", "volume", "session"]


@dataclass(frozen=True)
class MinuteBar:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    session: str

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def typical(self) -> float:
        return (self.high + self.low + self.close) / 3.0


def path_for(symbol: str):
    return MINUTE_DIR / f"{symbol.upper()}.csv"


def _parse_ts(raw) -> datetime | None:
    """Webull sends '2026-09-04T19:59:00.000+0000'; the store writes '...+00:00'."""
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw) / 1000.0, tz=timezone.utc)
    if not isinstance(raw, str):
        return None
    s = raw.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    elif len(s) > 5 and (s[-5] in "+-") and ":" not in s[-5:]:
        s = s[:-2] + ":" + s[-2:]          # +0000 -> +00:00
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def normalize(rows) -> list[MinuteBar]:
    """Webull rows -> validated bars. Every numeric field arrives as a string.

    Rows that fail the OHLC identity are dropped, not repaired. A bar whose open
    sits outside its own high/low is corrupt, and a pattern detector fed one
    reads it as an extraordinary signal rather than as bad data.
    """
    out: list[MinuteBar] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        ts = _parse_ts(r.get("time") or r.get("ts") or r.get("timestamp"))
        if ts is None:
            continue
        try:
            o = float(r["open"]); h = float(r["high"])
            lo = float(r["low"]); c = float(r["close"])
            v = float(r.get("volume") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if not (lo <= o <= h and lo <= c <= h and h >= lo) or lo <= 0:
            continue
        sess = str(r.get("trading_session") or r.get("session") or "RTH").upper()
        out.append(MinuteBar(ts, o, h, lo, c, v, sess if sess in SESSIONS else "RTH"))
    return out


def load(symbol: str, start: datetime | None = None, end: datetime | None = None,
         sessions: tuple[str, ...] | None = None) -> list[MinuteBar]:
    p = path_for(symbol)
    if not p.exists():
        return []
    out: list[MinuteBar] = []
    with p.open("r", encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            ts = _parse_ts(row.get("ts"))
            if ts is None:
                continue
            if start and ts < start:
                continue
            if end and ts > end:
                continue
            if sessions and row.get("session") not in sessions:
                continue
            try:
                out.append(MinuteBar(ts, float(row["open"]), float(row["high"]),
                                     float(row["low"]), float(row["close"]),
                                     float(row.get("volume") or 0),
                                     row.get("session") or "RTH"))
            except (KeyError, ValueError):
                continue
    return out


def ingest(symbol: str, rows) -> dict:
    """Merge a batch of Webull rows into the store, keyed on timestamp.

    Idempotent by construction: re-ingesting an overlapping window is a no-op
    rather than a duplicate. Paging backwards through history always overlaps at
    the seam, so this is the normal case, not an edge case.
    """
    if isinstance(rows, str):
        rows = json.loads(rows)
    fresh = normalize(rows)
    existing = {b.ts: b for b in load(symbol)}
    before = len(existing)
    for b in fresh:
        existing[b.ts] = b                      # newest write wins on conflict
    merged = [existing[k] for k in sorted(existing)]

    MINUTE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path_for(symbol).with_suffix(".csv.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(FIELDS)
        for b in merged:
            w.writerow([b.ts.isoformat(), b.open, b.high, b.low, b.close,
                        b.volume, b.session])
    tmp.replace(path_for(symbol))               # atomic: a killed harvest can't truncate the store

    return {
        "symbol": symbol.upper(),
        "received": len(rows or []),
        "valid": len(fresh),
        "rejected": len(rows or []) - len(fresh),
        "added": len(merged) - before,
        "total": len(merged),
        "first": merged[0].ts.isoformat() if merged else None,
        "last": merged[-1].ts.isoformat() if merged else None,
    }


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def plan(symbol: str, days: int = 30, sessions: str = "RTH",
         end: datetime | None = None) -> dict:
    """The exact MCP calls needed to cover `days` back from `end`.

    Webull pages BACKWARDS: it returns `count` bars ending at `end_time`, newest
    first. So each step's end_time is the previous step's oldest bar. The cursors
    here are computed from the calendar rather than from responses, which makes
    the plan printable up front — the fetch loop then walks it without having to
    decide anything.

    Requests already covered by the store are marked skip:true. Coverage is
    judged per UTC day at 60% of a full RTH session, which tolerates half-days
    and holidays without treating them as permanent gaps to re-fetch forever.
    """
    end = end or datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    n_sessions = len([s for s in sessions.split(",") if s.strip()])
    per_day = RTH_MINUTES_PER_DAY * max(n_sessions, 1)
    days_per_request = max(1, MAX_BARS_PER_REQUEST // per_day)

    # Count coverage from the START OF the first day in range, not from the
    # exact cutoff. `start` is a timestamp mid-session, so anchoring the lookup
    # there discards that morning's bars and reports an already-complete day
    # as a gap to re-fetch.
    cov_start = datetime.combine(start.date(), datetime.min.time(),
                                 tzinfo=timezone.utc)
    have_by_day: dict[str, int] = {}
    for b in load(symbol, start=cov_start, end=end):
        have_by_day[b.ts.date().isoformat()] = have_by_day.get(b.ts.date().isoformat(), 0) + 1

    # Cursors must land on TRADING days, not calendar days. A weekend end_time
    # gets silently clamped by Webull to the last available session, so a plan
    # built on calendar arithmetic quietly re-requests the same day and reports
    # a gap it can never close. Chunk the weekdays instead.
    from .market_hours import is_trading_day
    weekdays = []
    d = start.date()
    while d <= end.date():
        if is_trading_day(d):                   # excludes NYSE/Nasdaq holidays too
            weekdays.append(d)
        d += timedelta(days=1)
    weekdays.sort(reverse=True)                 # newest first: Webull pages backwards

    requests: list[dict] = []
    for i in range(0, len(weekdays), days_per_request):
        chunk = weekdays[i:i + days_per_request]
        newest, oldest = chunk[0], chunk[-1]
        # 23:59Z of the newest day. For US RTH (13:30-20:00Z) the UTC date and
        # the ET trading date coincide, so this captures the whole session
        # without needing to know whether EST or EDT is in force.
        cursor = datetime.combine(newest, datetime.min.time(),
                                  tzinfo=timezone.utc) + timedelta(hours=23, minutes=59)
        covered = sum(1 for d in chunk
                      if have_by_day.get(d.isoformat(), 0) >= per_day * 0.6)
        requests.append({
            "tool": "mcp__claude_ai_Webull__get_stock_bars_single",
            "args": {
                "symbol": symbol.upper(),
                "category": "US_STOCK",
                "timespan": "M1",
                "real_time_required": "false",
                "count": str(MAX_BARS_PER_REQUEST),
                "end_time": _ms(cursor),
                "trading_sessions": sessions,
            },
            "window": f"{oldest} -> {newest}",
            "trading_days": len(chunk),
            "skip": covered >= len(chunk),
        })
    todo = [r for r in requests if not r["skip"]]
    return {
        "symbol": symbol.upper(),
        "range": f"{start.date()} -> {end.date()}",
        "sessions": sessions,
        "bars_per_request": MAX_BARS_PER_REQUEST,
        "days_per_request": days_per_request,
        "requests": requests,
        "todo": len(todo),
        "note": ("Rate-limited: Webull returns HTTP 429 on rapid bursts. Issue these "
                 "SEQUENTIALLY, not in parallel, and ingest each response before the next. "
                 "A 390-bar response (~83KB) exceeds the MCP tool-result cap and is "
                 "auto-saved to disk, so ingest that file directly — the bars never need "
                 "to be retyped. Run the loop in a subagent anyway, to keep the "
                 "orchestrating context clean."),
    }


def gaps(symbol: str, min_bars: int = int(RTH_MINUTES_PER_DAY * 0.6)) -> list[str]:
    """Weekdays inside the stored range holding suspiciously few RTH bars."""
    bars = load(symbol, sessions=("RTH",))
    if not bars:
        return []
    by_day: dict[str, int] = {}
    for b in bars:
        k = b.ts.date().isoformat()
        by_day[k] = by_day.get(k, 0) + 1
    from .market_hours import is_trading_day
    first, last = bars[0].ts.date(), bars[-1].ts.date()
    out = []
    d = first
    while d <= last:
        if is_trading_day(d) and by_day.get(d.isoformat(), 0) < min_bars:
            out.append(f"{d.isoformat()} ({by_day.get(d.isoformat(), 0)} bars)")
        d += timedelta(days=1)
    return out


def splits_suspected(symbol: str, threshold: float = 0.35) -> list[str]:
    """Overnight jumps big enough to be a split rather than a move.

    Minute bars are unadjusted, so a 10:1 split prints as a -90% gap. Left
    undetected it would read as the crash of the century to any gap or drawdown
    logic downstream.
    """
    bars = load(symbol, sessions=("RTH",))
    out = []
    prev_day, prev_close = None, None
    for b in bars:
        day = b.ts.date()
        if prev_day is not None and day != prev_day and prev_close:
            move = b.open / prev_close - 1.0
            if abs(move) >= threshold:
                out.append(f"{prev_day} -> {day}: {move:+.1%}")
        if day != prev_day:
            prev_day = day
        prev_close = b.close
    return out


class SubscriptionRequired(RuntimeError):
    """The key works; it just isn't entitled to market data."""


def _market_data():
    """MarketData client built from WEBULL_APP_KEY / WEBULL_APP_SECRET in .env.

    Separate from the trade client in brokers/webull_openapi.py: data and trade
    have their own initializers in the SDK, and this one must not import the
    trade surface at all — there is no reason for a bar fetcher to hold an object
    with place_order on it.
    """
    import os
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    key, secret = os.getenv("WEBULL_APP_KEY", ""), os.getenv("WEBULL_APP_SECRET", "")
    if not (key and secret):
        raise SubscriptionRequired(
            "WEBULL_APP_KEY / WEBULL_APP_SECRET not set in .env")
    from webull.core.client import ApiClient
    from webull.data.data_client import ClientInitializer, MarketData
    api = ApiClient(key, secret, os.getenv("WEBULL_REGION_ID", "us"))
    ClientInitializer.initializer(api)
    return MarketData(api)


def _unwrap(resp):
    if resp is None:
        return []
    for attr in ("data", "result", "json"):
        v = getattr(resp, attr, None)
        if callable(v):
            try:
                v = v()
            except Exception:
                v = None
        if v is not None:
            return v
    return resp


def fetch(symbol: str, days: int = 30, sessions: str = "RTH",
          end: datetime | None = None, pause: float = 0.6,
          verbose: bool = True) -> dict:
    """Page minute bars backwards from the API straight into the store.

    This is the path that makes the portal self-sufficient. It requires an
    OpenAPI Advanced Quotes subscription on the .env key — note that an advanced
    quotes subscription bought in the Webull APP or desktop does NOT carry over;
    OpenAPI needs its own.

    Paging is driven by the response, not the calendar: each request's end_time
    is the oldest bar of the last one. Trusting the calendar instead would walk
    straight past holidays and re-request empty windows forever.
    """
    import time as _time
    md = _market_data()
    end = end or datetime.now(timezone.utc)
    stop_at = end - timedelta(days=days)
    cursor, total_added, requests, backoff = end, 0, 0, pause

    while cursor > stop_at:
        try:
            resp = md.get_history_bar(
                symbol=symbol.upper(), category="US_STOCK", timespan="M1",
                count=str(MAX_BARS_PER_REQUEST), real_time_required="false",
                trading_sessions=sessions, end_time=str(_ms(cursor)))
        except Exception as exc:
            msg = str(exc)
            if "403" in msg or "subscription" in msg.lower():
                raise SubscriptionRequired(
                    "Webull returned 403: this key has no OpenAPI market-data "
                    "subscription. Subscribe at developer.webull.com -> avatar -> "
                    "Advanced Quotes -> OpenAPI Advanced Quotes. An advanced-quotes "
                    "sub bought in the Webull app/desktop does NOT apply to OpenAPI."
                ) from exc
            if "429" in msg or "TOO_MANY_REQUESTS" in msg.upper():
                backoff = min(backoff * 2, 30.0)
                if verbose:
                    print(f"  429 rate-limited, backing off {backoff:.1f}s")
                _time.sleep(backoff)
                continue
            raise

        rows = _unwrap(resp)
        if isinstance(rows, dict):
            rows = rows.get("data") or rows.get("bars") or []
        bars = normalize(rows)
        requests += 1
        if not bars:
            if verbose:
                print(f"  no bars before {cursor.date()} — stopping (history exhausted)")
            break

        res = ingest(symbol, rows)
        total_added += res["added"]
        oldest = min(b.ts for b in bars)
        if verbose:
            print(f"  [{requests:>3}] {oldest.date()} -> {cursor.date()}  "
                  f"{len(bars):>5} bars, +{res['added']} new")
        # A page that returns only bars we already had means the cursor is not
        # advancing; without this the loop spins on the same minute forever.
        if oldest >= cursor:
            break
        cursor = oldest
        backoff = pause
        _time.sleep(pause)

    cov = coverage().get(symbol.upper(), {})
    return {"symbol": symbol.upper(), "requests": requests, "added": total_added,
            "stored": cov.get("bars", 0), "days": cov.get("days", 0),
            "first": cov.get("first"), "last": cov.get("last")}


def coverage() -> dict:
    if not MINUTE_DIR.exists():
        return {}
    out = {}
    for f in sorted(MINUTE_DIR.glob("*.csv")):
        bars = load(f.stem)
        if not bars:
            continue
        days = {b.ts.date() for b in bars}
        sess: dict[str, int] = {}
        for b in bars:
            sess[b.session] = sess.get(b.session, 0) + 1
        out[f.stem] = {
            "bars": len(bars),
            "days": len(days),
            "first": bars[0].ts.isoformat(),
            "last": bars[-1].ts.isoformat(),
            "sessions": sess,
            "mb": round(f.stat().st_size / 1e6, 1),
        }
    return out


def report() -> dict:
    """Roster entrypoint for the intraday_bars maker.

    Reports coverage and staleness only. It cannot refresh itself: the .env key
    authenticates but carries no market-data entitlement, so the store grows
    only via an in-session MCP harvest. That is a real dependency on a human and
    is stated here rather than left to look like an idle agent.
    """
    from .market_hours import is_trading_day
    cov = coverage()
    stale = []
    today = datetime.now(timezone.utc).date()
    for sym, v in cov.items():
        last = _parse_ts(v["last"])
        if not last:
            continue
        missed = sum(1 for i in range(1, (today - last.date()).days + 1)
                     if is_trading_day(last.date() + timedelta(days=i)))
        if missed >= 2:
            stale.append(f"{sym} ({missed} trading days behind)")
    return {
        "agent": "intraday_bars", "kind": "maker",
        "symbols": len(cov), "coverage": cov, "stale": stale,
        "self_refreshing": False,
        "blocked_by": ("no OpenAPI market-data entitlement on WEBULL_APP_KEY — "
                       "harvest in-session via the claude_ai_Webull connector, or "
                       "subscribe and use `--fetch`"),
    }


def _main() -> None:
    ap = argparse.ArgumentParser(description="Minute-bar store (fetched via MCP).")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--plan", metavar="SYMBOL")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--sessions", default="RTH")
    ap.add_argument("--ingest", nargs=2, metavar=("SYMBOL", "JSON_FILE"))
    ap.add_argument("--gaps", metavar="SYMBOL")
    ap.add_argument("--fetch", nargs="+", metavar="SYMBOL",
                    help="pull directly from the Webull API (needs the OpenAPI "
                         "Advanced Quotes subscription on WEBULL_APP_KEY)")
    args = ap.parse_args()

    if args.fetch:
        for sym in args.fetch:
            print(f"{sym.upper()}  fetching {args.days}d of M1 [{args.sessions}]")
            try:
                r = fetch(sym, days=args.days, sessions=args.sessions)
            except SubscriptionRequired as exc:
                print(f"\nBLOCKED: {exc}")
                return
            print(f"  done: +{r['added']} bars in {r['requests']} requests, "
                  f"store now {r['stored']} bars / {r['days']}d\n")
        return

    if args.ingest:
        sym, src = args.ingest
        data = json.loads(open(src, "r", encoding="utf-8").read())
        print(json.dumps(ingest(sym, data), indent=2))
        return

    if args.plan:
        p = plan(args.plan, days=args.days, sessions=args.sessions)
        print(f"{p['symbol']}  {p['range']}  sessions={p['sessions']}")
        print(f"{p['days_per_request']} days/request, {len(p['requests'])} requests, "
              f"{p['todo']} still needed\n")
        for i, r in enumerate(p["requests"], 1):
            print(f"  [{i:>2}] {r['window']}  end_time={r['args']['end_time']}"
                  f"{'   SKIP (have it)' if r['skip'] else ''}")
        print(f"\n{p['note']}")
        return

    if args.gaps:
        g = gaps(args.gaps)
        sp = splits_suspected(args.gaps)
        print(f"{args.gaps.upper()}: {len(g)} thin/missing weekday(s)")
        for x in g[:40]:
            print(f"  {x}")
        if sp:
            print("\nPossible splits (bars are UNADJUSTED):")
            for x in sp:
                print(f"  {x}")
        return

    cov = coverage()
    if not cov:
        print("Minute store empty. Fetch via the claude_ai_Webull MCP, then --ingest.")
        print("Start with:  python -m app.data.minute --plan MRVL --days 30")
        return
    print(f"Minute store: {len(cov)} symbol(s)")
    for sym, v in cov.items():
        sess = " ".join(f"{k}:{n}" for k, n in sorted(v["sessions"].items()))
        print(f"  {sym:6} {v['bars']:>7} bars  {v['days']:>4}d  {v['mb']:>5}MB  "
              f"{v['first'][:10]} -> {v['last'][:10]}  [{sess}]")


if __name__ == "__main__":
    _main()
