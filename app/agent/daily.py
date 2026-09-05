"""The Daily Agent — one autonomous routine, start to finish.

On each run it:
  1. Refreshes quotes for the full universe (your holdings + watchlist).
  2. Scores every symbol technically.
  3. Pulls an events briefing + geopolitical assessment.
  4. Asks the analyst (Claude, or heuristic fallback) for per-symbol ratings.
  5. Blends all sources into a conviction score per symbol (with breakdown).
  6. Sizes orders (conviction × volatility) and routes them through the
     executor — which honors the trading mode (paper / confirm / live) and the
     risk manager. Covered-call / CSP / sell-the-news option plans are attached
     for review.
  7. Produces a DailyReport (the "morning brief") and remembers it.

It is mode-aware and safe by construction: in paper mode it fills on the sim
broker, in confirm mode it queues orders for your tap, and even in live mode
every order still passes the risk gate and the kill-switch.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..analytics import ConvictionEngine
from ..analytics.technical import technical_score
from ..analysis import rsi
from ..engine.sizing import SizingParams, conviction_to_signal
from ..intel.claude_analyst import ClaudeAnalyst


@dataclass
class DailyReport:
    ts: float
    mode: str
    convictions: list[dict] = field(default_factory=list)      # ranked
    analyst_note: str = ""
    analyst_source: str = "heuristic"
    geo: dict = field(default_factory=dict)
    actions: list[dict] = field(default_factory=list)          # orders acted on
    option_plans: dict = field(default_factory=dict)
    campaign: dict = field(default_factory=dict)
    summary: str = ""

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "generated": time.strftime("%Y-%m-%d %H:%M", time.localtime(self.ts)),
            "mode": self.mode,
            "campaign": self.campaign,
            "summary": self.summary,
            "analyst_note": self.analyst_note,
            "analyst_source": self.analyst_source,
            "geo": self.geo,
            "convictions": self.convictions,
            "actions": self.actions,
            "option_plans": self.option_plans,
        }


class DailyAgent:
    def __init__(self, portal, analyst: ClaudeAnalyst | None = None,
                 conviction: ConvictionEngine | None = None,
                 sizing: SizingParams | None = None, execute: bool = True):
        self.portal = portal
        self.analyst = analyst or ClaudeAnalyst()
        self.conviction = conviction or ConvictionEngine()
        self.sizing = sizing or SizingParams()
        self.execute = execute
        self.last_report: DailyReport | None = None

    def run(self) -> DailyReport:
        p = self.portal
        p.ensure_built()
        symbols = p.held_symbols or p._all_symbols()

        # 1. quotes
        quotes = p.market.refresh(symbols)
        prices = {s: q.price for s, q in quotes.items()}

        # 2. technicals
        techs = {s: technical_score(s, p.market.history(s)) for s in symbols}

        # 3. events + geopolitics
        brief = p.intel.briefing(symbols)
        geo = brief.geo
        sentiment = brief.symbol_sentiment
        geo_bias = geo.get("ticker_bias", {})

        # 4. analyst (Claude or heuristic)
        positions = {pos.symbol: pos for pos in p.brokers["paper"].get_positions()} \
            if "paper" in p.brokers else {}
        symbol_data: list[dict[str, Any]] = []
        for s in symbols:
            hist = p.market.history(s)
            r = rsi(hist, 14)[-1] if len(hist) > 15 else None
            pos = positions.get(s)
            symbol_data.append({
                "symbol": s,
                "price": round(prices.get(s, 0.0), 2),
                "technical": round(techs[s].score, 3),
                "rsi": round(r, 1) if r is not None else None,
                "position_shares": pos.quantity if pos else 0,
                "avg_cost": pos.avg_price if pos else None,
                "unrealized_pct": round((prices.get(s, pos.avg_price) / pos.avg_price - 1) * 100, 2)
                if pos and pos.avg_price else None,
                "events": [e.headline for e in brief.events if s in e.symbols][:3],
            })
        analyst = self.analyst.analyze(symbol_data)

        # 5. blend conviction
        convictions = []
        for s in symbols:
            inputs = {
                "technical": techs[s].score if techs[s].ready else None,
                "analyst": analyst.ratings.get(s),
                "sentiment": sentiment.get(s),
                "geopolitical": geo_bias.get(s),
            }
            conv = self.conviction.blend(s, {k: v for k, v in inputs.items() if v is not None})
            convictions.append(conv)
        convictions = self.conviction.rank(convictions)

        # 6. size + route orders (campaign doctrine applies)
        camp = p.campaign
        halted = p.enforce_campaign()                       # drawdown halt trips kill-switch
        focus = set(camp.focus_symbols) if camp else set()
        derisk = camp.derisk_factor() if camp else 1.0
        from ..models import Side
        actions: list[dict] = []
        if self.execute and not halted:
            for conv in convictions:
                sig = conviction_to_signal(
                    conv.symbol, conv.score, p.market.history(conv.symbol),
                    strategy="daily_agent",
                    note=f"{conv.action} (conviction {conv.score:+.2f})",
                    params=self.sizing,
                )
                if sig is None:
                    continue
                # Concentration: only OPEN new longs in the focus names; sells
                # (trims to fund the focus / cut losers) are allowed anywhere.
                if sig.side is Side.BUY and focus and conv.symbol not in focus:
                    continue
                # Exit clock: scale position size down as the deadline approaches.
                sig.order_value *= derisk
                if sig.order_value <= 0:
                    continue
                p.signal_log.append(sig)
                result = p.executor.handle_signal(sig, prices.get(conv.symbol, 0.0))
                actions.append({
                    "symbol": conv.symbol, "side": sig.side.value,
                    "conviction": round(conv.score, 3), "focus": conv.symbol in focus,
                    "status": result.order.status.value, "detail": result.detail,
                })

        # 7. option plans + report
        option_plans = p.option_plans(days=30)
        campaign = camp.status(p._book_value()).to_dict() if camp else {}
        report = DailyReport(
            ts=time.time(), mode=p.settings.mode.value,
            convictions=[c.to_dict() for c in convictions],
            analyst_note=analyst.portfolio_note, analyst_source=analyst.source,
            geo=geo, actions=actions, option_plans=option_plans, campaign=campaign,
        )
        report.summary = self._summary(report, convictions)
        self.last_report = report
        return report

    @staticmethod
    def _summary(report: DailyReport, convictions) -> str:
        buys = [c.symbol for c in convictions if c.action in ("buy", "strong_buy")]
        sells = [c.symbol for c in convictions if c.action in ("sell", "strong_sell")]
        tilt = report.geo.get("risk_tilt", "neutral")
        parts = []
        c = report.campaign
        if c:
            if c.get("breached"):
                parts.append(f"⛔ CAMPAIGN HALT ({c['drawdown_pct']:.0f}% drawdown).")
            else:
                parts.append(f"${c['equity']:,.0f} → $1M ({c['progress_pct']:.0f}%), "
                             f"{c['days_remaining']}d left, needs {c['required_cagr_pct']:.0f}%/yr, "
                             f"pace {c['pace']}.")
        parts.append(f"Risk tilt: {tilt}.")
        if buys:
            parts.append(f"Bullish: {', '.join(buys[:6])}.")
        if sells:
            parts.append(f"Bearish: {', '.join(sells[:6])}.")
        if report.actions:
            filled = sum(1 for a in report.actions if a["status"] in ("filled", "submitted", "queued"))
            parts.append(f"{filled}/{len(report.actions)} orders actioned ({report.mode} mode).")
        else:
            parts.append("No orders met the conviction threshold.")
        return " ".join(parts)
