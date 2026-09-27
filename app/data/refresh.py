"""Daily-bar refresh — keeps data/prices and data/ohlc current, unattended.

THE BUG THIS FIXES

Both daily stores were written once, on 2026-09-04, and nothing ever wrote them
again. `MarketData.load_real_history` reads the CSV cache first and only falls
through to yfinance when the file is MISSING, never when it is OLD — so every
technical score, forecast, vol-scalar and backtest ran on a frozen September 4
while the dashboard looked healthy. Minute bars were being harvested the whole
time; the daily layer underneath them was not.

WHY A FULL RE-DOWNLOAD RATHER THAN APPENDING NEW BARS

The stores are split- AND dividend-adjusted (`auto_adjust=True`). A split or a
dividend rewrites every historical bar before it, so appending today's bar to
yesterday's file mixes two adjustment bases — the exact raw-vs-adjusted trap
that once showed NVDA's split as a -76% "saving". Re-pulling each file's full
range keeps a file internally consistent, and one batched request per ~40 names
makes that cheap.

WHAT IT REFUSES TO DO

A replacement is written only if it keeps everything the old file had: same
start, a last bar no earlier than before, and nearly every existing bar present.
A partial or truncated download is rejected and the old file stays. Writes are
atomic (temp file + rename), so a crash mid-run cannot leave a half-written CSV.
An in-progress session's bar is never stored — a 10:00 "close" is not a close.

    python -m app.data.refresh                 # everything cached + held names
    python -m app.data.refresh MRVL NVDA TSLA  # just these
    python -m app.data.refresh --status        # freshness report, no network
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from datetime import date, datetime, timedelta
from pathlib import Path

from ..config import ROOT
from .market_hours import _now_et, close_time_for, is_trading_day
from .ohlc import OHLC_DIR, UNTRADEABLE, universe

PRICES_DIR = ROOT / "data" / "prices"
STATUS_PATH = ROOT / "data" / "refresh_status.json"

BATCH = 40
DEFAULT_START = {"prices": "2022-01-01", "ohlc": "2018-01-01"}
# A replacement must return at least this share of the bars the old file had in
# the same date range. Below it, the download is treated as truncated.
MIN_COVERAGE = 0.97
# The close is only final a little after the bell; before this, today's bar is
# still moving and is not stored.
SETTLE_MINUTES = 20


# --- sessions -------------------------------------------------------------
def last_complete_session(now: datetime | None = None) -> date:
    """The most recent session whose daily bar is final."""
    now = now or _now_et()
    d = now.date()
    if is_trading_day(d):
        close = datetime.combine(d, close_time_for(d), tzinfo=now.tzinfo)
        if now >= close + timedelta(minutes=SETTLE_MINUTES):
            return d
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


# --- files ----------------------------------------------------------------
def _read_rows(path: Path) -> list[list[str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as fh:
        r = csv.reader(fh)
        next(r, None)
        return [row for row in r if row and row[0]]


def _write_atomic(path: Path, header: list[str], rows: list[list]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)
    os.replace(tmp, path)


def last_bar(path: Path) -> str | None:
    rows = _read_rows(path)
    return rows[-1][0] if rows else None


def accept(old: list[list[str]], new: list[list]) -> tuple[bool, str]:
    """May `new` replace `old`? Only if it loses nothing the old file had."""
    if not new:
        return False, "no data returned"
    if not old:
        return True, "new file"
    old_first, old_last = old[0][0], old[-1][0]
    new_first, new_last = new[0][0], new[-1][0]
    if new_last < old_last:
        return False, f"last bar would move back {old_last} -> {new_last}"
    slack = (date.fromisoformat(old_first) + timedelta(days=10)).isoformat()
    if new_first > slack:
        return False, f"history would shrink: starts {new_first}, had {old_first}"
    covered = sum(1 for r in new if old_first <= r[0] <= old_last)
    if covered < MIN_COVERAGE * len(old):
        return False, f"truncated: {covered} of {len(old)} existing bars returned"
    return True, f"+{len(new) - covered} bar(s)" if new_last > old_last else "no new bars"


# --- download -------------------------------------------------------------
def _num(x) -> float | None:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(v) else v


def _download(symbols: list[str], start: str, through: date) -> dict[str, list[list]]:
    """Batched adjusted OHLCV -> {symbol: [[date, o, h, l, c, v], ...]}."""
    import warnings
    warnings.filterwarnings("ignore")
    import yfinance as yf

    df = yf.download(symbols, start=start, progress=False, auto_adjust=True,
                     actions=False, group_by="ticker", threads=True)
    out: dict[str, list[list]] = {}
    if df is None or df.empty:
        return out
    multi = getattr(df.columns, "nlevels", 1) > 1
    tickers = set(df.columns.get_level_values(0)) if multi else set()
    cutoff = through.isoformat()
    for s in symbols:
        if multi and s not in tickers:
            continue
        sub = df[s] if multi else df
        rows = []
        for idx, r in sub.iterrows():
            d = idx.strftime("%Y-%m-%d")
            if d > cutoff:
                continue                    # today's bar, still forming
            o, h, lo, c = (_num(r.get("Open")), _num(r.get("High")),
                           _num(r.get("Low")), _num(r.get("Close")))
            if None in (o, h, lo, c):
                continue
            # Same corrupt-bar rule as ohlc.fetch: a pattern detector reads an
            # impossible bar as an extraordinary signal.
            if not (lo <= o <= h and lo <= c <= h):
                continue
            rows.append([d, o, h, lo, c, _num(r.get("Volume")) or 0.0])
        if rows:
            out[s] = rows
    return out


# --- targets --------------------------------------------------------------
def _held() -> list[str]:
    try:
        import yaml
        h = yaml.safe_load((ROOT / "config" / "holdings.yaml").read_text("utf-8")) or {}
        return [str(x["symbol"]).upper() for x in h.get("holdings", []) if x.get("symbol")]
    except Exception:
        return []


def targets(symbols: list[str] | None = None,
            stores: tuple[str, ...] = ("prices", "ohlc")) -> dict[str, set[str]]:
    """Which store each symbol belongs in: {symbol: {"prices", "ohlc"}}.

    Default scope is what is already cached plus the live book, so a new
    holding gets history on the next run. The close store is not widened to the
    whole OHLC universe: a dozen modules scan data/prices/, and a refresh
    should keep data current, not silently change what those scans cover.
    """
    out: dict[str, set[str]] = {}

    def add(sym: str, store: str) -> None:
        s = sym.upper()
        if s not in UNTRADEABLE:
            out.setdefault(s, set()).add(store)

    if symbols:
        for s in symbols:
            for st_ in stores:
                add(s, st_)
        return out
    for p in PRICES_DIR.glob("*.csv"):
        add(p.stem, "prices")
    for p in OHLC_DIR.glob("*.csv"):
        add(p.stem, "ohlc")
    for s in _held():
        add(s, "prices")
        add(s, "ohlc")
    for s in universe(include_held=False):
        add(s, "ohlc")
    return out


def _path(store: str, sym: str) -> Path:
    return (PRICES_DIR if store == "prices" else OHLC_DIR) / f"{sym}.csv"


# --- run ------------------------------------------------------------------
def refresh(symbols: list[str] | None = None, now: datetime | None = None,
            downloader=_download, stores: tuple[str, ...] = ("prices", "ohlc")) -> dict:
    """Bring every target file current. Returns the status it also writes.

    `stores` limits NEW symbols to one store — e.g. ohlc only for a research
    universe, so the close store (scanned by a dozen modules) is not widened.
    """
    through = last_complete_session(now)
    plan = targets(symbols, stores)
    names = sorted(plan)
    written, unchanged, failed = 0, 0, {}

    for i in range(0, len(names), BATCH):
        batch = names[i:i + BATCH]
        old = {(st, s): _read_rows(_path(st, s)) for s in batch for st in plan[s]}
        firsts = [rows[0][0] for rows in old.values() if rows]
        firsts += [DEFAULT_START[st] for (st, s), rows in old.items() if not rows]
        try:
            got = downloader(batch, min(firsts), through)
        except Exception as exc:                        # network, rate limit, ...
            for s in batch:
                failed[s] = f"{type(exc).__name__}: {exc}"
            continue
        for s in batch:
            bars = got.get(s)
            for st in sorted(plan[s]):
                prev = old[(st, s)]
                start = prev[0][0] if prev else DEFAULT_START[st]
                rows = [b for b in (bars or []) if b[0] >= start]
                if st == "prices":
                    rows = [[b[0], b[4]] for b in rows]
                ok, why = accept(prev, rows)
                if not ok:
                    failed[f"{s} ({st})"] = why
                    continue
                if prev and rows[-1][0] == prev[-1][0] and len(rows) == len(prev):
                    unchanged += 1
                    continue
                header = (["date", "close"] if st == "prices"
                          else ["date", "open", "high", "low", "close", "volume"])
                _write_atomic(_path(st, s), header, rows)
                written += 1

    status = {"ran_at": (now or _now_et()).isoformat(timespec="seconds"),
              "expected_last_bar": through.isoformat(),
              "written": written, "unchanged": unchanged, "failed": failed,
              **freshness(through)}
    # Only a full run speaks for the whole store; a one-symbol catch-up from
    # load_real_history must not overwrite that record with a partial one.
    if symbols is None:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATUS_PATH.write_text(json.dumps(status, indent=2), encoding="utf-8")
    return status


def freshness(through: date | None = None) -> dict:
    """Which cached files are behind the last completed session. No network."""
    through = through or last_complete_session()
    stale: dict[str, str] = {}
    total = 0
    for store, d in (("prices", PRICES_DIR), ("ohlc", OHLC_DIR)):
        for p in d.glob("*.csv"):
            if p.stem.upper() in UNTRADEABLE:
                continue                    # no feed exists; stale by definition
            total += 1
            lb = last_bar(p)
            if not lb or lb < through.isoformat():
                stale[f"{p.stem} ({store})"] = lb or "empty"
    return {"files": total, "stale": stale}


def _main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("symbols", nargs="*")
    ap.add_argument("--status", action="store_true", help="report freshness only")
    ap.add_argument("--ohlc-only", action="store_true", help="new symbols go to data/ohlc only")
    a = ap.parse_args()
    if a.status:
        through = last_complete_session()
        f = freshness(through)
        print(f"expected last bar {through}: {f['files'] - len(f['stale'])}/{f['files']} files current")
        for k, v in sorted(f["stale"].items()):
            print(f"  stale  {k:<18} last {v}")
        return
    s = refresh(a.symbols or None, stores=("ohlc",) if a.ohlc_only else ("prices", "ohlc"))
    print(f"expected last bar {s['expected_last_bar']}: wrote {s['written']}, "
          f"unchanged {s['unchanged']}, failed {len(s['failed'])}, "
          f"still stale {len(s['stale'])}/{s['files']}")
    for k, v in sorted(s["failed"].items()):
        print(f"  FAILED {k:<18} {v}")
    # Nonzero exit when a real share failed, so the scheduled task shows it.
    raise SystemExit(1 if len(s["stale"]) > 0.05 * max(s["files"], 1) else 0)


if __name__ == "__main__":
    _main()
