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


CSS = """
:root{--ground:#F6F7F5;--panel:#FFFFFF;--ink:#16201B;--muted:#5F6B64;--rule:#D9DED9;
--accent:#1F6F52;--ok:#2E7D4F;--ok-bg:#E3F1E8;--warn:#B7791F;--warn-bg:#FBF0DC;
--crit:#B42318;--crit-bg:#FBE4E2;--chip:#EDF0ED}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){color-scheme:dark;
--ground:#0F1412;--panel:#161D1A;--ink:#E6ECE8;--muted:#93A09A;--rule:#26302B;--accent:#5CC29A;
--ok:#6FD39B;--ok-bg:#15291E;--warn:#E3B45C;--warn-bg:#2A2212;--crit:#F08A80;--crit-bg:#2E1715;--chip:#1E2622}}
:root[data-theme="dark"]{color-scheme:dark;--ground:#0F1412;--panel:#161D1A;--ink:#E6ECE8;
--muted:#93A09A;--rule:#26302B;--accent:#5CC29A;--ok:#6FD39B;--ok-bg:#15291E;--warn:#E3B45C;
--warn-bg:#2A2212;--crit:#F08A80;--crit-bg:#2E1715;--chip:#1E2622}
body{background:var(--ground);color:var(--ink);font:15px/1.5 "IBM Plex Sans",system-ui,sans-serif;
padding-inline:16px;padding-block:20px 40px}
main{max-width:760px;margin:0 auto;display:flex;flex-direction:column;gap:22px}
h1,h2{font-family:"IBM Plex Sans Condensed","IBM Plex Sans",system-ui,sans-serif;text-wrap:balance;margin:0}
h1{font-size:22px;font-weight:600}
h2{font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--muted)}
td.n,.fig b,.chip{font-family:"IBM Plex Mono",ui-monospace,monospace;font-variant-numeric:tabular-nums}
.meta{color:var(--muted);font-size:13px;margin:4px 0 0}
.status{border-radius:8px;padding:12px 14px;font-weight:600;display:flex;flex-direction:column;gap:6px}
.status.ok{background:var(--ok-bg);color:var(--ok)}
.status.bad{background:var(--crit-bg);color:var(--crit)}
.status ul{margin:0;padding-left:18px;font-weight:400;color:var(--ink)}
.figs{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.fig{background:var(--panel);border:1px solid var(--rule);border-radius:8px;padding:10px 12px;
display:flex;flex-direction:column;gap:2px}
.fig span,.fig small{font-size:12px;color:var(--muted)}
.fig b{font-size:20px;font-weight:500}
.fig b.halt{color:var(--crit);font-weight:600}
.scroll{overflow-x:auto;border:1px solid var(--rule);border-radius:8px;background:var(--panel)}
table{border-collapse:collapse;width:100%;font-size:13px}
th,td{padding:7px 10px;text-align:left;border-bottom:1px solid var(--rule);white-space:nowrap}
tr:last-child td{border-bottom:0}
th{font-weight:600;color:var(--muted);font-size:12px}
td.n,th.n{text-align:right}
.tag{font-size:11px;padding:1px 6px;border-radius:999px;background:var(--chip);color:var(--muted)}
.tag.gate{background:var(--ok-bg);color:var(--ok)}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{font-size:13px;padding:3px 8px;border-radius:6px;background:var(--chip)}
.chip.s1{background:var(--warn-bg);color:var(--warn)}
.note{font-size:12px;color:var(--muted);margin:0}
.jobs{display:flex;flex-direction:column;gap:6px;margin:0;padding:0;list-style:none}
.jobs li{display:flex;gap:10px;align-items:baseline;font-size:14px}
.pill{font-size:11px;font-weight:600;padding:1px 7px;border-radius:999px;flex:none}
.pill.ok{background:var(--ok-bg);color:var(--ok)}
.pill.bad{background:var(--crit-bg);color:var(--crit)}
.jobs .d{color:var(--muted)}
.pos{color:var(--ok)}
.neg{color:var(--crit)}
section{display:flex;flex-direction:column;gap:10px}
"""


def _pct(x) -> str:
    if x is None:
        return "—"
    cls = "pos" if x > 0 else "neg" if x < 0 else ""
    return f'<span class="{cls}">{x:+.2f}%</span>'


def to_html(r: dict) -> str:
    """The phone page. Tokens + both themes per the artifact page contract."""
    e = html.escape
    day = r["paper_day"] or "—"
    out = ["<title>Trading Daily Sheet</title>",
           '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500'
           '&family=IBM+Plex+Sans+Condensed:wght@600&family=IBM+Plex+Sans:wght@400;600&display=swap">',
           f"<style>{CSS}</style><main>",
           f'<header><h1>Trading daily — {e(day)}</h1><p class="meta">Generated {e(r["generated"])}'
           f' · paper session {r.get("sessions") or 0} of the 40-session validation</p></header>']
    fails = r["failed_jobs"]
    if fails:
        items = "".join(f"<li><b>{e(c['job'])}</b> — {e(c['detail'])}</li>" for c in fails)
        out.append(f'<div class="status bad">{len(fails)} job(s) need attention<ul>{items}</ul></div>')
    else:
        out.append(f'<div class="status ok">All {len(r["checks"])} scheduled jobs produced their output</div>')

    c = r["campaign"]
    if "error" in c:
        out.append(f'<p class="note">Campaign unavailable: {e(c["error"])}</p>')
    else:
        dd = '<b class="halt">HALTED</b>' if c["breached"] else f'<b>{c["drawdown_pct"]}%</b>'
        g = r["gate"]
        out.append('<section><h2>Book</h2><div class="figs">'
                   f'<div class="fig"><span>Core book</span><b>${c["equity"]:,.0f}</b>'
                   f'<small>{c["progress_pct"]}% of $1M · pace {e(c["pace"])}</small></div>'
                   f'<div class="fig"><span>Drawdown from peak</span>{dd}'
                   f'<small>halt at {c["drawdown_halt_pct"]}%</small></div>'
                   f'<div class="fig"><span>Live capital allowed</span><b>${g.get("recommended_sleeve_usd") or 0:,}</b>'
                   f'<small>gate: {e(str(g.get("gate") or "—"))}</small></div></div>'
                   f'<p class="note">Net deposits since start ${c.get("net_contributions", 0):,.0f} — '
                   'counted toward $1M, excluded from drawdown and pace.</p></section>')

    rows = []
    for t, s in r["tracks"].items():
        kind = ('<span class="tag gate">counts</span>' if s.get("counts_toward_gate")
                else '<span class="tag">shadow</span>' if t.endswith("i") else '<span class="tag">control</span>')
        ref = s.get("reference") or {}
        refs = f'{100 * ref["win_rate"]:.0f}% · {ref["expectancy"]:+.2f}%' if ref else "—"
        if s.get("trades"):
            rows.append(f"<tr><td>{e(t)} {kind}</td><td class=n>{s['trades']}</td><td class=n>{s['open']}</td>"
                        f"<td class=n>{100 * s['win_rate']:.0f}%</td><td class=n>{_pct(s['expectancy'])}</td>"
                        f"<td class=n>{_pct(s.get('edge_vs_control'))}</td>"
                        f"<td class=n>${s['pnl_usd']:,.0f}</td><td class=n>{refs}</td></tr>")
        else:
            rows.append(f"<tr><td>{e(t)} {kind}</td><td class=n>0</td>"
                        f"<td class=n>{s.get('open', 0)} +{s.get('pending', 0)}</td>"
                        "<td class=n>—</td><td class=n>—</td><td class=n>—</td><td class=n>—</td>"
                        f"<td class=n>{refs}</td></tr>")
    out.append('<section><h2>rev1 paper scorecard</h2><div class="scroll"><table><tr><th>track</th>'
               '<th class=n>closed</th><th class=n>open</th><th class=n>win</th><th class=n>per trade</th>'
               '<th class=n>edge</th><th class=n>P&amp;L</th><th class=n>backtest</th></tr>'
               + "".join(rows) + '</table></div><p class="note">S1 capitulation bounce · S2 dip buy. '
               'Edge = return per trade minus the no-signal control with the same exit. '
               '"+n" = entries waiting for the next open. Backtest = win rate · return per trade.</p></section>')

    sig = sorted({(x["symbol"], x["track"]) for x in r["signals_today"]})
    chips = "".join(f'<span class="chip{" s1" if tr == "rev1.S1" else ""}">{e(sym)}</span>' for sym, tr in sig)
    out.append(f'<section><h2>Signals on the {e(day)} close · {len(sig)}</h2>'
               f'<div class="chips">{chips or "<span class=note>none</span>"}</div>'
               '<p class="note">Paper entries fill at the next open. Amber = S1 capitulation bounce.</p></section>')

    if r["closed_today"]:
        lis = "".join(f"<tr><td>{e(t['symbol'])}</td><td>{e(t['track'])}</td>"
                      f"<td class=n>{_pct(t['ret_net'])}</td><td class=n>{t['hold']}d</td></tr>"
                      for t in r["closed_today"])
        out.append('<section><h2>Closed today</h2><div class="scroll"><table><tr><th>symbol</th>'
                   f'<th>track</th><th class=n>return</th><th class=n>held</th></tr>{lis}</table></div></section>')
    if r["preview"]:
        pv = "".join(f'<span class="chip">{e(p["symbol"])}</span>' for p in r["preview"])
        out.append(f'<section><h2>Likely next signals</h2><div class="chips">{pv}</div>'
                   f'<p class="note">Below their levels as of {e(str(r["preview_as_of"]))} ET — '
                   'only a close below confirms.</p></section>')

    jobs = "".join(f'<li><span class="pill {"ok" if j["ok"] else "bad"}">{"OK" if j["ok"] else "FAIL"}</span>'
                   f'<span><b>{e(j["job"])}</b> <span class="d">{e(j["detail"])}</span></span></li>'
                   for j in r["checks"])
    out.append(f'<section><h2>Scheduled jobs · checked {e(str(r["health_checked"]))}</h2>'
               f'<ul class="jobs">{jobs}</ul></section></main>')
    return "\n".join(out)


def write() -> Path:
    r = build()
    md = to_markdown(r)
    OUT.mkdir(parents=True, exist_ok=True)
    day = r["paper_day"] or datetime.now().date().isoformat()
    (OUT / f"daily_{day}.md").write_text(md, encoding="utf-8")
    (OUT / "daily_latest.md").write_text(md, encoding="utf-8")
    (OUT / "daily_latest.html").write_text(to_html(r), encoding="utf-8")
    (OUT / "daily_latest.json").write_text(json.dumps(r, indent=1, default=str), encoding="utf-8")
    return OUT / "daily_latest.md"


if __name__ == "__main__":
    p = write()
    print(p.read_text("utf-8"))
