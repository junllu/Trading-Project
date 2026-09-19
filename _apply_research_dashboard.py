"""Integrate researcher into dashboard, screening, decisions, sentiment, launch."""
from __future__ import annotations

from pathlib import Path

# ---------- sentiment tracker ----------
Path("app/analytics/sentiment_track.py").write_text(r'''"""Sentiment tracking — book + research shortlist in one snapshot.

Persists data/sentiment_latest.json for the dashboard and decision brief.
Does not place orders.
"""
from __future__ import annotations

import json
import time
from typing import Any

from ..config import ROOT

OUT = ROOT / "data" / "sentiment_latest.json"
HISTORY = ROOT / "data" / "sentiment_history.jsonl"


def snapshot(portal=None, research_brief: dict | None = None) -> dict[str, Any]:
    symbols: list[str] = []
    if portal is not None:
        try:
            symbols = list(portal.held_symbols or portal._all_symbols() or [])
        except Exception:
            symbols = []
    brief = research_brief
    if brief is None:
        try:
            from .researcher import latest
            brief = latest()
        except Exception:
            brief = {}
    shortlist = list((brief.get("discovery") or {}).get("shortlist") or [])

    book_sent: dict[str, float] = {}
    events_n = 0
    geo = {}
    if portal is not None and symbols:
        try:
            intel = portal.ensure_built().intel.briefing(symbols)
            book_sent = dict(intel.symbol_sentiment or {})
            events_n = len(intel.events or [])
            geo = intel.geo or {}
        except Exception as exc:
            geo = {"error": str(exc)}

    # Research candidates: reuse intel if overlapping, else mark unknown
    research_sent: dict[str, float | None] = {}
    for s in shortlist:
        research_sent[s] = book_sent.get(s)

    # Aggregate tilt
    vals = [v for v in book_sent.values() if isinstance(v, (int, float))]
    avg = sum(vals) / len(vals) if vals else None
    bullish = sum(1 for v in vals if v > 0.15)
    bearish = sum(1 for v in vals if v < -0.15)

    out = {
        "as_of": time.strftime("%Y-%m-%d %H:%M:%S"),
        "book": {
            "symbols": len(symbols),
            "avg_sentiment": round(avg, 3) if avg is not None else None,
            "bullish_count": bullish,
            "bearish_count": bearish,
            "by_symbol": {k: round(v, 3) for k, v in sorted(book_sent.items(), key=lambda x: -abs(x[1]))[:30]},
            "events": events_n,
        },
        "research_shortlist": {
            "symbols": shortlist,
            "sentiment": research_sent,
            "mcp_queue": (brief or {}).get("mcp_queue") or [],
        },
        "geo": {
            "risk_tilt": geo.get("risk_tilt"),
            "confidence": geo.get("confidence"),
            "matched_themes": geo.get("matched_themes") or [],
        },
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    with HISTORY.open("a", encoding="utf-8") as f:
        f.write(json.dumps(out) + "\n")
    return out


def report() -> dict[str, Any]:
    if OUT.exists():
        try:
            return json.loads(OUT.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return snapshot()
''', encoding="utf-8")
print("wrote sentiment_track.py")

# ---------- screening facade ----------
Path("app/analytics/screening_board.py").write_text(r'''"""Screening board — tradability screen + discovery + researcher shortlist."""
from __future__ import annotations

import json
import time
from typing import Any

from ..config import ROOT

OUT = ROOT / "data" / "screening_latest.json"


def report() -> dict[str, Any]:
    tradable = []
    try:
        from .screener import ScreenCriteria
        # screener may expose screen()/report — best effort
        import app.analytics.screener as sc
        for name in ("report", "screen", "run"):
            fn = getattr(sc, name, None)
            if callable(fn):
                try:
                    out = fn()
                    if isinstance(out, dict):
                        tradable = out.get("passed") or out.get("symbols") or out
                    elif isinstance(out, list):
                        tradable = out
                    break
                except TypeError:
                    try:
                        out = fn(ScreenCriteria.core())
                        if isinstance(out, list):
                            tradable = out
                    except Exception:
                        pass
                except Exception:
                    pass
    except Exception as exc:
        tradable = {"error": str(exc)}

    research = {}
    try:
        from .researcher import latest
        research = latest()
    except Exception as exc:
        research = {"error": str(exc)}

    discovery = {}
    try:
        from .discovery import report as dreport
        discovery = dreport()
    except Exception as exc:
        discovery = {"error": str(exc)}

    out = {
        "as_of": time.strftime("%Y-%m-%d %H:%M:%S"),
        "tradability": tradable if not isinstance(tradable, list) else [
            (t.to_dict() if hasattr(t, "to_dict") else t) for t in tradable[:40]
        ],
        "discovery": discovery,
        "researcher_shortlist": (research.get("discovery") or {}).get("shortlist") or [],
        "mcp_queue": research.get("mcp_queue") or [],
        "note": "Screening informs decisions; it does not auto-buy. Buys still need sleeve + theme + approval.",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    return out
''', encoding="utf-8")
print("wrote screening_board.py")

# ---------- API ----------
routes = Path("app/api/routes.py")
rt = routes.read_text(encoding="utf-8")
if "/research" not in rt:
    insert = '''
# --- researcher / screening / sentiment ------------------------------------
@router.get("/research")
def research_get():
    """Latest researcher coverage brief (maker output)."""
    from ..analytics.researcher import latest
    return latest()


@router.post("/research/refresh")
def research_refresh():
    """Re-run researcher brief (discovery + dossiers)."""
    from ..analytics.researcher import report
    return report()


@router.get("/screening")
def screening_get():
    from ..analytics.screening_board import report
    return report()


@router.get("/sentiment")
def sentiment_get():
    from ..analytics.sentiment_track import report, snapshot
    data = report()
    if not data.get("as_of"):
        return snapshot(portal.ensure_built())
    return data


@router.post("/sentiment/refresh")
def sentiment_refresh():
    from ..analytics.sentiment_track import snapshot
    return snapshot(portal.ensure_built())

'''
    # before performance or sleeve
    marker = "# --- performance / autonomy"
    if marker not in rt:
        marker = "# --- capital sleeve"
    if marker not in rt:
        raise SystemExit("no API insert marker")
    rt = rt.replace(marker, insert + "\n" + marker, 1)
    routes.write_text(rt, encoding="utf-8")
    print("routes: research/screening/sentiment added")
else:
    print("routes already have /research")

# ---------- portal startup: run researcher in background ----------
portal = Path("app/portal.py")
pt = portal.read_text(encoding="utf-8")
if "research_on_startup" not in pt:
    needle = '''        if auto_cfg.get("schedule_autostart", True):
            try:
                self.scheduler.start()
            except Exception:
                pass
'''
    add = '''        if auto_cfg.get("schedule_autostart", True):
            try:
                self.scheduler.start()
            except Exception:
                pass

        # Researcher + sentiment on boot (background) so launch.bat surfaces a brief
        # without blocking the dashboard. Maker only — no orders.
        if auto_cfg.get("research_on_startup", True):
            import threading
            def _boot_research():
                try:
                    from .analytics.researcher import report as research_report
                    brief = research_report()
                    from .analytics.sentiment_track import snapshot as sent_snap
                    sent_snap(self, brief)
                    from .analytics.screening_board import report as screen_report
                    screen_report()
                except Exception:
                    pass
            threading.Thread(target=_boot_research, name="boot-researcher", daemon=True).start()
'''
    if needle not in pt:
        raise SystemExit("portal schedule_autostart block missing")
    pt = pt.replace(needle, add, 1)
    portal.write_text(pt, encoding="utf-8")
    print("portal: research_on_startup thread")
else:
    print("portal already boots researcher")

# ---------- autonomy cycle: refresh research first ----------
ops = Path("app/agent/autonomy_ops.py")
ot = ops.read_text(encoding="utf-8")
if "research_report" not in ot:
    old = '''    from ..analytics.performance import snapshot, trade_policy_from_sleeve
    from ..analytics.sleeve import evaluate_sleeve

    snap = snapshot(portal)
'''
    new = '''    from ..analytics.performance import snapshot, trade_policy_from_sleeve
    from ..analytics.sleeve import evaluate_sleeve

    try:
        from ..analytics.researcher import report as research_report
        brief = research_report()
        from ..analytics.sentiment_track import snapshot as sent_snap
        sent_snap(portal, brief)
        from ..analytics.screening_board import report as screen_report
        screen_report()
    except Exception:
        pass

    snap = snapshot(portal)
'''
    if old not in ot:
        print("WARN: autonomy_ops not patched")
    else:
        ops.write_text(ot.replace(old, new, 1), encoding="utf-8")
        print("autonomy_ops: research+sentiment each cycle")

# ---------- daily decisions: attach screening + sentiment to analyze/plan ----------
daily = Path("app/agent/daily.py")
dt = daily.read_text(encoding="utf-8")
# enrich return of _analyze if needed — find end of _analyze return
if "research_shortlist" not in dt.split("def _analyze")[1][:3500]:
    # Find convictions loop end and where _analyze returns
    # Look for return { after convictions
    marker = '        return {\n            "prices": prices, "convictions": convictions,'
    # try common pattern
    import re
    m = re.search(r"return \{\s*\n\s*\"prices\": prices", dt)
    if not m:
        # patch after curated/feeds section by adding research into inputs context
        # Simpler: patch build_plan plays to include screening + sentiment
        print("will enrich plays only")
    else:
        print("found analyze return")

# Enrich plays block
if '"screening"' not in dt:
    old_plays_research = '''            "research_brief": {
                "shortlist": (research_brief.get("discovery") or {}).get("shortlist"),
                "mcp_queue": research_brief.get("mcp_queue"),
                "as_of": research_brief.get("as_of"),
                "written_to": research_brief.get("written_to"),
            },
'''
    new_plays_research = '''            "research_brief": {
                "shortlist": (research_brief.get("discovery") or {}).get("shortlist"),
                "mcp_queue": research_brief.get("mcp_queue"),
                "as_of": research_brief.get("as_of"),
                "written_to": research_brief.get("written_to"),
            },
            "screening": (lambda: (__import__("app.analytics.screening_board", fromlist=["report"]).report()))(),
            "sentiment": (lambda: (__import__("app.analytics.sentiment_track", fromlist=["snapshot"]).snapshot)(p, research_brief))(),
'''
    # cleaner without lambda
    new_plays_research = '''            "research_brief": {
                "shortlist": (research_brief.get("discovery") or {}).get("shortlist"),
                "mcp_queue": research_brief.get("mcp_queue"),
                "as_of": research_brief.get("as_of"),
                "written_to": research_brief.get("written_to"),
            },
'''
    # Insert before plays = after research_brief load
    inject = '''        screening_snap = {}
        sentiment_snap = {}
        try:
            from ..analytics.screening_board import report as _screen_rep
            screening_snap = _screen_rep()
        except Exception as exc:
            screening_snap = {"error": str(exc)}
        try:
            from ..analytics.sentiment_track import snapshot as _sent_snap
            sentiment_snap = _sent_snap(p, research_brief)
        except Exception as exc:
            sentiment_snap = {"error": str(exc)}
'''
    if "screening_snap" not in dt:
        dt = dt.replace(
            "        research_brief = {}\n        try:\n            from ..analytics.researcher import latest as research_latest\n",
            "        research_brief = {}\n        try:\n            from ..analytics.researcher import latest as research_latest\n",
            1,
        )
        # after research_brief assignment block
        anchor = '''        except Exception as exc:
            research_brief = {"error": str(exc)}
        plays = {
'''
        if anchor not in dt:
            # try alternate quotes
            print("WARN daily anchor missing", dt.find("research_brief"))
        else:
            dt = dt.replace(
                anchor,
                '''        except Exception as exc:
            research_brief = {"error": str(exc)}
''' + inject + '''        plays = {
''',
                1,
            )
            dt = dt.replace(
                '''            "research_brief": {
                "shortlist": (research_brief.get("discovery") or {}).get("shortlist"),
                "mcp_queue": research_brief.get("mcp_queue"),
                "as_of": research_brief.get("as_of"),
                "written_to": research_brief.get("written_to"),
            },
''',
                '''            "research_brief": {
                "shortlist": (research_brief.get("discovery") or {}).get("shortlist"),
                "mcp_queue": research_brief.get("mcp_queue"),
                "as_of": research_brief.get("as_of"),
                "written_to": research_brief.get("written_to"),
            },
            "screening": screening_snap,
            "sentiment": sentiment_snap,
''',
                1,
            )
            daily.write_text(dt, encoding="utf-8")
            print("daily: screening+sentiment in plays")
else:
    print("daily plays already have screening")

# Decision: when building buy list, annotate orders if symbol on research shortlist
dt = daily.read_text(encoding="utf-8")
if "on_research_shortlist" not in dt:
    # in orders.append section add note
    old_notes = '''                if conv.symbol in focus:
                    notes.append("focus name")
'''
    new_notes = '''                if conv.symbol in focus:
                    notes.append("focus name")
                try:
                    from ..analytics.researcher import latest as _rl
                    _sl = ((_rl().get("discovery") or {}).get("shortlist") or [])
                    if conv.symbol in _sl:
                        notes.append("on researcher shortlist")
                except Exception:
                    pass
'''
    if old_notes in dt:
        daily.write_text(dt.replace(old_notes, new_notes, 1), encoding="utf-8")
        print("daily: research shortlist note on orders")

# ---------- config ----------
cfg = Path("config/config.yaml")
ct = cfg.read_text(encoding="utf-8")
if "research_on_startup" not in ct:
    ct = ct.replace(
        "  live_auto: false",
        "  live_auto: false\n  research_on_startup: true   # brief + sentiment + screening on launch.bat",
        1,
    )
    cfg.write_text(ct, encoding="utf-8")
    print("config: research_on_startup")

# ---------- launch.bat ----------
bat = Path("launch.bat")
bt = bat.read_text(encoding="utf-8", errors="replace")
if "researcher" not in bt.lower():
    bt = bt.replace(
        "echo   Starting the portal...",
        "echo   Starting the portal...\n"
        "echo   On boot: live recorder + orchestrator + daily schedule + researcher brief\n"
        "echo   (research/sentiment/screening refresh in background — see dashboard cards)",
        1,
    )
    bat.write_text(bt, encoding="utf-8")
    print("launch.bat: startup message")
else:
    print("launch.bat already mentions researcher")

# ---------- dashboard HTML ----------
dash = Path("app/web/templates/dashboard.html")
html = dash.read_text(encoding="utf-8")

card = '''
    <!-- ============ RESEARCHER / SCREENING / SENTIMENT ============ -->
    <section class="card col-4" id="researchCard" style="border-color:#4a3a5a;">
      <h2>Researcher
        <span class="pill" id="researchAsOf" style="margin-left:8px;">—</span>
      </h2>
      <div style="display:flex;gap:8px;margin-bottom:8px;">
        <button onclick="refreshResearch(true)">Refresh brief</button>
      </div>
      <div class="muted" style="font-size:12px;margin-bottom:6px;">Shortlist (not owned)</div>
      <div id="researchShortlist" class="muted">—</div>
      <div class="muted" style="font-size:12px;margin-top:10px;">MCP queue</div>
      <div id="researchMcp" class="muted" style="font-size:11px;">—</div>
    </section>

    <section class="card col-4" id="screenCard" style="border-color:#3a4a5a;">
      <h2>Screening</h2>
      <div class="kpis" style="margin-bottom:8px;">
        <div><div class="stat" id="screenDisc">—</div><div class="stat sub">Discovery hits</div></div>
        <div><div class="stat" id="screenShort">—</div><div class="stat sub">Research n</div></div>
      </div>
      <div id="screenNote" class="muted" style="font-size:12px;">Informs decisions — does not auto-buy.</div>
      <div id="screenList" class="muted" style="font-size:12px;margin-top:8px;">—</div>
    </section>

    <section class="card col-4" id="sentCard" style="border-color:#5a3a4a;">
      <h2>Sentiment
        <span class="pill" id="sentTilt" style="margin-left:8px;">—</span>
      </h2>
      <div style="display:flex;gap:8px;margin-bottom:8px;">
        <button onclick="refreshSentiment(true)">Refresh</button>
      </div>
      <div class="kpis">
        <div><div class="stat" id="sentAvg">—</div><div class="stat sub">Book avg</div></div>
        <div><div class="stat" id="sentBull">—</div><div class="stat sub">Bullish</div></div>
        <div><div class="stat" id="sentBear">—</div><div class="stat sub">Bearish</div></div>
      </div>
      <div id="sentTop" class="muted" style="font-size:12px;margin-top:8px;">—</div>
    </section>

'''

if 'id="researchCard"' not in html:
    anchor = '    <div class="zone">The book</div>'
    if anchor not in html:
        # after sleeve card
        sleeve_end = '    </section>\n\n    <div class="zone">'
        # find sleeveCard closing then The book
        idx = html.find('id="sleeveCard"')
        if idx < 0:
            raise SystemExit("no sleeve card")
        # find The book after sleeve
        book = html.find('<div class="zone">The book</div>', idx)
        if book < 0:
            book = html.find("The book", idx)
            raise SystemExit("The book not found after sleeve")
        # find start of line for The book zone
        line = html.rfind("\n", 0, book)
        html = html[: line + 1] + card + html[line + 1 :]
    else:
        html = html.replace(anchor, card + anchor, 1)
    print("dashboard: cards inserted")
else:
    print("dashboard cards exist")

js = '''
    async function refreshResearch(force){
      try {
        const s = force
          ? await api('/api/research/refresh','POST')
          : await (await fetch('/api/research')).json();
        const disc = s.discovery || {};
        const list = disc.shortlist || [];
        document.getElementById('researchAsOf').textContent = s.as_of || '—';
        document.getElementById('researchShortlist').innerHTML = list.length
          ? list.map(x => '<span class="pill" style="margin:2px;">'+x+'</span>').join('')
          : '—';
        const mcp = s.mcp_queue || disc.needs_mcp || [];
        document.getElementById('researchMcp').textContent = mcp.length
          ? mcp.slice(0,6).join(' · ') : 'none';
        // screening bits from same brief
        document.getElementById('screenShort').textContent = list.length;
      } catch(e) {
        document.getElementById('researchShortlist').textContent = 'unavailable: '+e;
      }
    }
    async function refreshScreening(){
      try {
        const s = await (await fetch('/api/screening')).json();
        const disc = s.discovery || {};
        document.getElementById('screenDisc').textContent = disc.in_live_themes ?? disc.scanned ?? '—';
        const sl = s.researcher_shortlist || [];
        document.getElementById('screenShort').textContent = sl.length;
        document.getElementById('screenList').innerHTML = sl.length
          ? sl.map(x => '<span class="pill" style="margin:2px;">'+x+'</span>').join('')
          : '—';
        document.getElementById('screenNote').textContent = s.note || '';
      } catch(e) {
        document.getElementById('screenNote').textContent = 'screening unavailable';
      }
    }
    async function refreshSentiment(force){
      try {
        const s = force
          ? await api('/api/sentiment/refresh','POST')
          : await (await fetch('/api/sentiment')).json();
        const book = s.book || {};
        const geo = s.geo || {};
        document.getElementById('sentTilt').textContent = (geo.risk_tilt || '—')+'';
        document.getElementById('sentAvg').textContent =
          book.avg_sentiment == null ? '—' : (book.avg_sentiment >= 0 ? '+' : '') + book.avg_sentiment;
        document.getElementById('sentBull').textContent = book.bullish_count ?? '—';
        document.getElementById('sentBear').textContent = book.bearish_count ?? '—';
        const by = book.by_symbol || {};
        const top = Object.entries(by).slice(0,8)
          .map(([k,v]) => k+':'+(v>=0?'+':'')+v).join(' · ');
        document.getElementById('sentTop').textContent = top || '—';
      } catch(e) {
        document.getElementById('sentTop').textContent = 'unavailable: '+e;
      }
    }

'''

if "function refreshResearch" not in html:
    if "async function refreshSleeve" in html:
        html = html.replace("async function refreshSleeve", js + "    async function refreshSleeve", 1)
    else:
        html = html.replace("async function refresh()", js + "    async function refresh()", 1)
    print("dashboard: js added")

if "refreshResearch(false)" not in html:
    html = html.replace(
        "refresh(); refreshSleeve(false); refreshIntel();",
        "refresh(); refreshSleeve(false); refreshResearch(false); refreshScreening(); refreshSentiment(false); refreshIntel();",
        1,
    )
    print("dashboard: hooked refresh")

dash.write_text(html, encoding="utf-8")
print("dashboard written")

# smoke test file
Path("tests/test_research_integration.py").write_text('''"""Researcher integration smoke."""
from app.analytics.researcher import report
from app.analytics.sentiment_track import snapshot
from app.analytics.screening_board import report as screen_report
from app.agent.roster import BY_NAME, validate


def test_researcher_still_on_roster():
    assert BY_NAME["researcher"].kind == "maker"
    assert validate() == []


def test_screening_board_structure():
    s = screen_report()
    assert "researcher_shortlist" in s
    assert "discovery" in s


def test_sentiment_snapshot_structure():
    s = snapshot(None, {"discovery": {"shortlist": ["AAA"]}, "mcp_queue": []})
    assert "book" in s and "research_shortlist" in s
''', encoding="utf-8")
print("DONE")
