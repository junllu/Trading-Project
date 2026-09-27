"""Daily summary — one page answering "is everything OK, and how is it doing?"

    python -m app.reports.daily_summary          # writes data/reports/daily_latest.{html,md}

Built for reading on a phone while away. It reads ONLY the files the scheduled
jobs already write (ops health, rev1 paper ledger, campaign state, holdings,
price cache) — it runs no strategy and touches no broker, so a broken report
can never break trading, and a report that is itself stale says so.

The first thing on the page is whether every job ran, because every failure
this project has had was silent and looked healthy on the dashboard.
"""
from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path

from ..config import ROOT

DATA = ROOT / "data"
OUT = DATA / "reports"


def _read(p: Path, default=None):
    try:
        return json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return default


def _last_close(sym: str) -> float | None:
    p = DATA / "ohlc" / f"{sym}.csv"
    try:
        last = p.read_text("utf-8").strip().splitlines()[-1].split(",")
        return float(last[4])
    except (OSError, IndexError, ValueError):
        return None


def campaign() -> dict:
    """Real book at the latest cached close, judged like the portal does."""
    from ..campaign.campaign import Campaign, load_state
    from ..portfolio.holdings import load_cash, load_holdings, load_other_value
    hs = load_holdings()
    book = float(load_cash() or 0.0) + load_other_value()
    for h in hs:
        px = _last_close(str(h["symbol"]).upper()) or float(h.get("last") or 0.0)
        book += float(h.get("shares") or 0.0) * px
    st = load_state()
    c = Campaign(start_capital=float(st.get("start_capital") or book),
                 started=st.get("started", "2026-09-05"),
                 high_water_mark=float(st.get("high_water_mark") or 0.0),
                 contributions=list(st.get("contributions") or []))
    return c.status(book).to_dict()          # state_path None: the report never writes it


def build() -> dict:
    health = _read(DATA / "ops_health.json", {}) or {}
    score = _read(DATA / "paper" / "rev1" / "scorecard.json", {}) or {}
    state = _read(DATA / "paper" / "rev1" / "state.json", {}) or {}
    preview = _read(DATA / "paper" / "rev1" / "preview.json", {}) or {}
    sleeve = _read(DATA / "sleeve_status.json", {}) or {}
    sig_path = DATA / "paper" / "rev1" / "signals.jsonl"
    last_day = state.get("last_close_run")
    todays = []
    if sig_path.exists() and last_day:
        for line in sig_path.read_text("utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("date") == last_day:
                todays.append(r)
    trades_path = DATA / "paper" / "rev1" / "trades.jsonl"
    closed_today = []
    if trades_path.exists() and last_day:
        for line in trades_path.read_text("utf-8").splitlines():
            try:
                t = json.loads(line)
            except ValueError:
                continue
            if t.get("exit_date") == last_day and not t["track"].endswith(("C1", "C3")):
                closed_today.append(t)
    try:
        camp = campaign()
    except Exception as exc:                            # never let one section kill the page
        camp = {"error": f"{type(exc).__name__}: {exc}"}
    checks = health.get("checks", [])
    return {"generated": datetime.now().isoformat(timespec="minutes"),
            "health_checked": health.get("checked_at"),
            "failed_jobs": [c for c in checks if not c.get("ok")],
            "checks": checks, "campaign": camp, "paper_day": last_day,
            "sessions": score.get("sessions"), "tracks": score.get("tracks", {}),
            "signals_today": todays, "closed_today": closed_today,
            "preview": preview.get("candidates_if_close_holds", [])[:15],
            "preview_as_of": preview.get("as_of_et"),
            "gate": {k: sleeve.get(k) for k in ("gate", "recommended_sleeve_usd", "missing")}}


def to_markdown(r: dict) -> str:
    L = [f"# Trading daily — {r['paper_day'] or '—'}", f"_generated {r['generated']}_", ""]
    if r["failed_jobs"]:
        L.append(f"## ⚠️ {len(r['failed_jobs'])} job(s) FAILED")
        L += [f"- **{c['job']}** ({c['day']}): {c['detail']}" for c in r["failed_jobs"]]
    else:
        L.append(f"## ✅ All {len(r['checks'])} jobs OK")
    c = r["campaign"]
    L += ["", "## Campaign"]
    if "error" in c:
        L.append(f"- unavailable: {c['error']}")
    else:
        L += [f"- Book **${c['equity']:,.0f}** · {c['progress_pct']}% of $1M · pace **{c['pace']}**",
              f"- Drawdown {c['drawdown_pct']}% (halt at {c['drawdown_halt_pct']}%)"
              + (" · ⛔ **HALTED**" if c["breached"] else ""),
              f"- Net deposits since start ${c.get('net_contributions', 0):,.0f}"]
    L += ["", f"## rev1 paper — {r['sessions']} session(s)",
          "| track | trades | open | win% | exp% | edge | $pnl | backtest |", "|---|---|---|---|---|---|---|---|"]
    for t, s in r["tracks"].items():
        ref = s.get("reference") or {}
        refs = f"win {100 * ref['win_rate']:.0f}% exp {ref['expectancy']:+.2f}" if ref else \
            ("shadow" if t.endswith("i") else "control")
        if s.get("trades"):
            L.append(f"| {t} | {s['trades']} | {s['open']} | {100 * s['win_rate']:.0f} | "
                     f"{s['expectancy']:+.2f} | {s.get('edge_vs_control', 0):+.2f} | "
                     f"{s['pnl_usd']:,.0f} | {refs} |")
        else:
            L.append(f"| {t} | 0 | {s.get('open', 0)} (+{s.get('pending', 0)} pending) | | | | | {refs} |")
    L += ["", f"## Today's signals ({len(r['signals_today'])})",
          ", ".join(sorted({s['symbol'] + ('*' if s['track'] == 'rev1.S1' else '')
                            for s in r["signals_today"]})) or "none",
          "_* = S1 capitulation bounce; others S2 dip buy. Fill at next open._"]
    if r["closed_today"]:
        L += ["", "## Closed today"]
        L += [f"- {t['track']} {t['symbol']}: {t['ret_net']:+.2f}% over {t['hold']}d" for t in r["closed_today"]]
    if r["preview"]:
        L += ["", f"## Tomorrow's likely candidates (as of {r['preview_as_of']})",
              ", ".join(p["symbol"] for p in r["preview"])]
    g = r["gate"]
    L += ["", f"## Capital gate: {g.get('gate')} — live ${g.get('recommended_sleeve_usd')}"]
    return "\n".join(L) + "\n"


def to_html(md: str) -> str:
    """Minimal, phone-readable HTML from the markdown above (no dependencies)."""
    out, in_table = [], False
    for line in md.splitlines():
        esc = html.escape(line)
        if line.startswith("|"):
            if set(line.replace("|", "").strip()) <= set("-"):
                continue
            cells = [c.strip() for c in line.strip("|").split("|")]
            tag = "th" if not in_table else "td"
            if not in_table:
                out.append("<table>")
                in_table = True
            out.append("<tr>" + "".join(f"<{tag}>{html.escape(c)}</{tag}>" for c in cells) + "</tr>")
            continue
        if in_table:
            out.append("</table>")
            in_table = False
        if line.startswith("## "):
            out.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("# "):
            out.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("- "):
            out.append(f"<p class=b>• {esc[2:]}</p>")
        elif line.strip():
            out.append(f"<p>{esc}</p>")
    if in_table:
        out.append("</table>")
    body = "\n".join(out).replace("**", "")
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            "<title>Trading daily</title><style>body{font:15px/1.45 system-ui;margin:16px;max-width:720px}"
            "table{border-collapse:collapse;font-size:13px;width:100%}td,th{border-bottom:1px solid #ddd;"
            "padding:4px 6px;text-align:left}h2{margin-top:22px;font-size:17px}.b{margin:2px 0}"
            "@media(prefers-color-scheme:dark){body{background:#111;color:#eee}td,th{border-color:#333}}"
            "</style>" + body)


def write() -> Path:
    r = build()
    md = to_markdown(r)
    OUT.mkdir(parents=True, exist_ok=True)
    day = r["paper_day"] or datetime.now().date().isoformat()
    (OUT / f"daily_{day}.md").write_text(md, encoding="utf-8")
    (OUT / "daily_latest.md").write_text(md, encoding="utf-8")
    (OUT / "daily_latest.html").write_text(to_html(md), encoding="utf-8")
    (OUT / "daily_latest.json").write_text(json.dumps(r, indent=1, default=str), encoding="utf-8")
    return OUT / "daily_latest.md"


if __name__ == "__main__":
    p = write()
    print(p.read_text("utf-8"))
