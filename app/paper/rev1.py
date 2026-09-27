"""rev1 — the frozen reversal strategy, paper-traded forward, track by track.

    python -m app.paper.rev1 close        # after the close: fills, exits, new signals
    python -m app.paper.rev1 intraday     # during the session: shadow entries + preview
    python -m app.paper.rev1 score        # scorecard vs the backtest

WHAT IS FROZEN (2026-09-27) AND WHY

The two entry/exit pairs that passed docs/prereg/2026-09-27-entry-exit-v4.md:

  rev1.S1   capitulation bounce  bd55 + weak rel. strength + SPY < 200dma, hold 5
  rev1.S2   dip buy              bd20, stop -2 ATR / target +3 ATR / 20 sessions

Nothing here may change for the 40-session validation. A refinement is a NEW
version (rev2, …) with its own tracks and ledger, run alongside — never an edit
to rev1 — so rev1's evidence always describes one fixed thing.

TRACKS, EACH GRADED INDEPENDENTLY

  rev1.S1 / rev1.S2     the tested strategy: decide on the CLOSE, enter at the
                        next OPEN. Exits are computed by the backtest's own
                        `simulate()` on the daily bars, so paper and backtest
                        cannot disagree on mechanics.
  rev1.S1i / rev1.S2i   SHADOW (user request): enter the moment a live price
                        breaks the level intraday instead of waiting for the
                        close. Untested; logged for comparison, never counted
                        toward any gate until it has its own pre-registration.
  rev1.C1 / rev1.C3     CONTROL: every name, every 20 sessions, no signal, with
                        S1's and S2's exits. "Edge" in the backtest was measured
                        against exactly this; measuring it forward the same way
                        is the only like-for-like comparison.

Every trade takes $1,000 notional and every signal is taken (one open position
per name per track), matching the backtest, which also took every signal.
Capital-constrained selection is a live-sizing question for later.
"""
from __future__ import annotations

import argparse
import json
import statistics as st
from datetime import datetime
from pathlib import Path

from ..analytics.features import _atr
from ..backtest.breakout_regime_study import PREREG
from ..backtest.costs import CostModel
from ..backtest.entry_exit_study import simulate
from ..config import ROOT
from ..data.market_hours import _now_et
from ..data.ohlc import Bar, load

VERSION = "rev1"
FROZEN = "2026-09-27"
DIR = ROOT / "data" / "paper" / VERSION
STATE = DIR / "state.json"
TRADES = DIR / "trades.jsonl"
SIGNALS = DIR / "signals.jsonl"
PREVIEW = DIR / "preview.json"
SCORE = DIR / "scorecard.json"

NOTIONAL = 1000.0
CONTROL_EVERY = 20
COST = CostModel.retail_equity().round_trip_bps() / 100.0

TRACKS = {
    "rev1.S1":  {"entry": "S1", "exit": "X1", "mode": "close"},
    "rev1.S2":  {"entry": "S2", "exit": "X3", "mode": "close"},
    "rev1.S1i": {"entry": "S1", "exit": "X1", "mode": "intraday"},
    "rev1.S2i": {"entry": "S2", "exit": "X3", "mode": "intraday"},
    "rev1.C1":  {"entry": "C",  "exit": "X1", "mode": "close"},
    "rev1.C3":  {"entry": "C",  "exit": "X3", "mode": "close"},
}
# Backtest reference (v4 scorecard, 2015-2026, 533 names) for side-by-side.
REFERENCE = {
    "rev1.S1": {"win_rate": 0.538, "avg_win": 6.29, "avg_loss": -6.76, "expectancy": 0.26,
                "edge_vs_control": 2.20},
    "rev1.S2": {"win_rate": 0.499, "avg_win": 7.85, "avg_loss": -5.87, "expectancy": 0.84,
                "edge_vs_control": 0.68},
}


def universe() -> list[str]:
    u = json.loads(PREREG.read_text("utf-8"))
    return sorted(set(u["existing_study"] + u["new_study"] + u["holdout_names"]))


# --- state ------------------------------------------------------------------
def _load() -> dict:
    try:
        return json.loads(STATE.read_text("utf-8"))
    except (OSError, ValueError):
        return {"version": VERSION, "frozen": FROZEN, "positions": [], "sessions": 0,
                "last_close_run": None}


def _save(s: dict) -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_name("state.json.tmp")
    tmp.write_text(json.dumps(s, indent=1), encoding="utf-8")
    tmp.replace(STATE)


def _append(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    DIR.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


# --- signals (identical definitions to breakout_study.detect) ---------------
def _fresh_break(bars: list[Bar], i: int, n: int, price: float | None = None) -> bool:
    """Close (or a live `price` for bar i) breaks below the prior n-session low,
    having not been below it the session before."""
    if i < n + 1:
        return False
    lvl = min(x.low for x in bars[i - n: i])
    lvl_prev = min(x.low for x in bars[i - n - 1: i - 1])
    px = bars[i].close if price is None else price
    return px < lvl and bars[i - 1].close >= lvl_prev


def _rel_str_rank(data: dict[str, list[Bar]], spy: dict[str, float], i_of: dict[str, int]) -> dict[str, float]:
    """63-session return minus SPY's, ranked across the universe (0 weakest)."""
    vals = {}
    for s, bars in data.items():
        i = i_of.get(s)
        if i is None or i < 63:
            continue
        d_now, d_then = bars[i].date, bars[i - 63].date
        if d_now in spy and d_then in spy:
            own = bars[i].close / bars[i - 63].close - 1
            vals[s] = own - (spy[d_now] / spy[d_then] - 1)
    order = sorted(vals, key=vals.get)
    n = len(order) - 1
    return {s: (k / n if n else 0.5) for k, s in enumerate(order)}


def _market_down(spy_bars: list[Bar], i: int) -> bool:
    if i < 199:
        return False
    return spy_bars[i].close < sum(b.close for b in spy_bars[i - 199: i + 1]) / 200


# --- shadow exits: entry at an intraday price on bar k ------------------------
def _shadow_exit(bars: list[Bar], k: int, entry: float, atr: float, exit_id: str):
    """Like simulate(), but entry is mid-bar k: bar k's range is not used for
    stops/targets (part of it happened before the fill)."""
    def done(px, j):
        return (px / entry - 1) * 100, j - k + 1, j
    if exit_id == "X1":
        j = k + 4
        return done(bars[j].close, j) if j < len(bars) else None
    stop, tgt = entry - 2 * atr, entry + 3 * atr
    for j in range(k + 1, min(k + 20, len(bars))):
        b = bars[j]
        if b.low <= stop:
            return done(min(b.open, stop), j)
        if b.high >= tgt:
            return done(max(b.open, tgt), j)
    j = k + 19
    return done(bars[j].close, j) if j < len(bars) else None


# --- the close job ------------------------------------------------------------
def close_job(today: str | None = None, ensure_fresh: bool = True) -> dict:
    from ..data.refresh import last_complete_session, refresh
    D = today or last_complete_session().isoformat()
    if ensure_fresh:
        try:
            status = json.loads((ROOT / "data" / "refresh_status.json").read_text("utf-8"))
        except (OSError, ValueError):
            status = {}
        if status.get("expected_last_bar", "") < D:
            refresh()                              # never decide on yesterday's bars

    s = _load()
    if s.get("last_close_run") == D:
        return {"skipped": f"already ran for {D}"}
    syms = universe()
    data: dict[str, list[Bar]] = {}
    for sym in syms:
        bars = load(sym)
        if bars and bars[-1].date == D:
            data[sym] = bars
    spy_bars = load("SPY")
    spy = {b.date: b.close for b in spy_bars}
    if not spy_bars or spy_bars[-1].date != D:
        return {"error": f"SPY has no bar for {D}; nothing decided"}
    i_of = {sym: len(b) - 1 for sym, b in data.items()}
    s["sessions"] = int(s.get("sessions") or 0) + 1

    closed, opened_fills = [], 0
    still = []
    for p in s["positions"]:
        bars = data.get(p["symbol"])
        if bars is None:
            still.append(p)                        # no bar today: decide tomorrow
            continue
        idx = {b.date: n for n, b in enumerate(bars)}
        spec = TRACKS[p["track"]]
        if spec["mode"] == "close":
            i = idx.get(p["signal_date"])
            if i is None:
                still.append(p)
                continue
            if p["status"] == "pending" and i + 1 < len(bars):
                p["status"], p["entry_date"] = "open", bars[i + 1].date
                p["entry_price"] = bars[i + 1].open
                opened_fills += 1
            r = simulate(bars, i, spec["exit"]) if p["status"] == "open" else None
        else:
            k = idx.get(p["entry_date"])
            r = (_shadow_exit(bars, k, p["entry_price"], p["atr"], spec["exit"])
                 if k is not None else None)
        if r is None:
            still.append(p)
            continue
        gross, hold, j = r
        p.update(status="closed", exit_date=bars[j].date,
                 exit_price=round(p["entry_price"] * (1 + gross / 100), 4),
                 ret_net=round(gross - COST, 4), hold=hold,
                 pnl=round(NOTIONAL * (gross - COST) / 100, 2))
        closed.append(p)
    s["positions"] = still
    _append(TRADES, closed)

    # New signals on today's close -> pending entries at tomorrow's open.
    held = {(p["track"], p["symbol"]) for p in s["positions"]}
    rs = _rel_str_rank(data, spy, i_of)
    mkt_down = _market_down(spy_bars, len(spy_bars) - 1)
    control_day = (s["sessions"] - 1) % CONTROL_EVERY == 0
    new, sig_rows = [], []
    for sym, bars in data.items():
        i = len(bars) - 1
        fires = {
            "S1": _fresh_break(bars, i, 55) and rs.get(sym, 1.0) <= 0.3 and mkt_down,
            "S2": _fresh_break(bars, i, 20),
            "C": control_day,
        }
        for track, spec in TRACKS.items():
            if spec["mode"] != "close" or not fires[spec["entry"]]:
                continue
            if (track, sym) in held:
                continue                           # one position per name per track
            new.append({"track": track, "symbol": sym, "signal_date": D, "status": "pending",
                        "atr": round(_atr(bars, i), 4)})
            if spec["entry"] != "C":
                sig_rows.append({"date": D, "track": track, "symbol": sym,
                                 "close": bars[i].close, "rel_str_rank": round(rs.get(sym, -1), 3),
                                 "spy_below_200dma": mkt_down, "version": VERSION})
    s["positions"].extend(new)
    _append(SIGNALS, sig_rows)
    s["last_close_run"] = D
    _save(s)
    sc = scorecard()
    return {"date": D, "names_with_bar": len(data), "fills": opened_fills,
            "closed": len(closed), "new_signals": {t: sum(1 for n in new if n["track"] == t)
                                                   for t in TRACKS if TRACKS[t]["mode"] == "close"},
            "open_positions": len(s["positions"]), "spy_below_200dma": mkt_down,
            "scorecard": sc}


# --- the intraday job -----------------------------------------------------------
def intraday_job() -> dict:
    from ..data.market_hours import state as session_state
    from ..data.tradingview_quotes import TradingViewQuotes
    st_ = session_state()
    if not st_.is_open:
        return {"skipped": f"market {st_.session}"}
    D = _now_et().date().isoformat()
    syms = universe()
    tv = {s: s.replace("-", ".") for s in syms}                 # BRK-B -> BRK.B
    got = TradingViewQuotes().fetch(list(tv.values()))
    live = {s: got[t] for s, t in tv.items() if t in got}
    if not live:
        return {"error": "no live quotes"}

    s = _load()
    data = {sym: load(sym) for sym in syms if sym in live}
    data = {k: v for k, v in data.items() if v and v[-1].date < D}   # through yesterday
    spy_bars = [b for b in load("SPY") if b.date < D]
    spy = {b.date: b.close for b in spy_bars}
    i_prev = {sym: len(b) - 1 for sym, b in data.items()}
    rs = _rel_str_rank(data, spy, i_prev)                       # known at yesterday's close
    mkt_down = _market_down(spy_bars, len(spy_bars) - 1)
    held = {(p["track"], p["symbol"]) for p in s["positions"]}

    new, preview = [], []
    now = _now_et().strftime("%H:%M")
    for sym, bars in data.items():
        px = live[sym]
        # Bar i is today's, still forming: append a stub so the level math is
        # identical to the close definition, with the live price as the "close".
        stub = bars + [Bar(D, px, px, px, px)]
        i = len(stub) - 1
        b20, b55 = _fresh_break(stub, i, 20, px), _fresh_break(stub, i, 55, px)
        fires = {"S1": b55 and rs.get(sym, 1.0) <= 0.3 and mkt_down, "S2": b20}
        if b20 or b55:
            preview.append({"symbol": sym, "price": px, "below_20d_low": b20,
                            "below_55d_low": b55, "s1_conditions": fires["S1"]})
        for track, spec in TRACKS.items():
            if spec["mode"] != "intraday" or not fires[spec["entry"]] or (track, sym) in held:
                continue
            new.append({"track": track, "symbol": sym, "signal_date": D, "entry_date": D,
                        "entry_time_et": now, "entry_price": px, "status": "open",
                        "atr": round(_atr(bars, len(bars) - 1), 4)})
            held.add((track, sym))
    # Stop proximity for the tested S2 positions — information only; the paper
    # fill is decided on the daily bar at the close, exactly as backtested.
    alerts = []
    for p in s["positions"]:
        if p["track"] == "rev1.S2" and p["status"] == "open" and p["symbol"] in live:
            stop = p["entry_price"] - 2 * p["atr"]
            if live[p["symbol"]] <= stop:
                alerts.append({"symbol": p["symbol"], "price": live[p["symbol"]], "stop": round(stop, 2)})
    s["positions"].extend(new)
    _save(s)
    DIR.mkdir(parents=True, exist_ok=True)
    PREVIEW.write_text(json.dumps({"as_of_et": f"{D} {now}", "candidates_if_close_holds": preview,
                                   "s2_stop_touched": alerts}, indent=1), encoding="utf-8")
    return {"as_of_et": f"{D} {now}", "quoted": len(live), "shadow_entries": len(new),
            "preview": len(preview), "stop_alerts": len(alerts)}


# --- scorecard -------------------------------------------------------------------
def _stats(trades: list[dict]) -> dict:
    if not trades:
        return {"trades": 0}
    r = [t["ret_net"] for t in trades]
    w, l = [x for x in r if x > 0], [x for x in r if x <= 0]
    return {"trades": len(r), "win_rate": round(len(w) / len(r), 3),
            "avg_win": round(st.mean(w), 2) if w else 0.0,
            "avg_loss": round(st.mean(l), 2) if l else 0.0,
            "payoff": round(abs(st.mean(w) / st.mean(l)), 2) if w and l else None,
            "expectancy": round(st.mean(r), 3), "pnl_usd": round(sum(t["pnl"] for t in trades), 2),
            "first_exit": min(t["exit_date"] for t in trades),
            "last_exit": max(t["exit_date"] for t in trades)}


def scorecard() -> dict:
    trades = []
    if TRADES.exists():
        trades = [json.loads(x) for x in TRADES.read_text("utf-8").splitlines() if x.strip()]
    s = _load()
    out = {"version": VERSION, "frozen": FROZEN, "sessions": s.get("sessions", 0),
           "as_of": datetime.now().isoformat(timespec="seconds"), "tracks": {}}
    for t in TRACKS:
        mine = [x for x in trades if x["track"] == t]
        row = _stats(mine) | {
            "open": sum(1 for p in s["positions"] if p["track"] == t and p["status"] == "open"),
            "pending": sum(1 for p in s["positions"] if p["track"] == t and p["status"] == "pending"),
            "counts_toward_gate": t in ("rev1.S1", "rev1.S2"),
            "reference": REFERENCE.get(t)}
        out["tracks"][t] = row
    for t, c in (("rev1.S1", "rev1.C1"), ("rev1.S2", "rev1.C3")):
        a, b = out["tracks"][t], out["tracks"][c]
        if a.get("trades") and b.get("trades"):
            out["tracks"][t]["edge_vs_control"] = round(a["expectancy"] - b["expectancy"], 3)
    DIR.mkdir(parents=True, exist_ok=True)
    SCORE.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def _print_score(sc: dict) -> None:
    print(f"rev1 scorecard — {sc['sessions']} session(s) since {sc['frozen']}")
    print(f"  {'track':<10}{'trades':>7}{'open':>6}{'pend':>6}{'win%':>7}{'avgW':>7}{'avgL':>7}"
          f"{'exp%':>7}{'edge':>7}{'$pnl':>9}   backtest ref")
    for t, r in sc["tracks"].items():
        ref = r.get("reference") or {}
        refs = (f"win {100 * ref['win_rate']:.0f}% exp {ref['expectancy']:+.2f} "
                f"edge {ref['edge_vs_control']:+.2f}") if ref else ""
        tag = "" if r["counts_toward_gate"] else ("  shadow" if t.endswith("i") else "  control")
        if r.get("trades"):
            print(f"  {t:<10}{r['trades']:>7}{r['open']:>6}{r['pending']:>6}"
                  f"{100 * r['win_rate']:>7.1f}{r['avg_win']:>7.2f}{r['avg_loss']:>7.2f}"
                  f"{r['expectancy']:>7.2f}{r.get('edge_vs_control', 0):>7.2f}{r['pnl_usd']:>9.0f}   {refs}{tag}")
        else:
            print(f"  {t:<10}{0:>7}{r['open']:>6}{r['pending']:>6}{'':>44}   {refs}{tag}")


def _main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("job", choices=["close", "intraday", "score"])
    a = ap.parse_args()
    if a.job == "close":
        r = close_job()
        print(json.dumps({k: v for k, v in r.items() if k != "scorecard"}, indent=1))
        if "scorecard" in r:
            _print_score(r["scorecard"])
        return 1 if "error" in r else 0
    if a.job == "intraday":
        print(json.dumps(intraday_job(), indent=1))
        return 0
    _print_score(scorecard())
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())
