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

import json
import time
from dataclasses import dataclass, field
from typing import Any

from ..analytics import ConvictionEngine
from ..analytics.technical import technical_score
from ..config import ROOT
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
                 sizing: SizingParams | None = None, execute: bool = True,
                 forecaster=None):
        from ..ml import build_forecaster
        self.portal = portal
        self.analyst = analyst or ClaudeAnalyst()
        self.conviction = conviction or ConvictionEngine()
        self.sizing = sizing or SizingParams()
        self.execute = execute
        self.forecaster = forecaster or build_forecaster("naive")
        self.last_report: DailyReport | None = None

    def _analyze(self) -> dict[str, Any]:
        """Shared analysis pipeline: quotes -> technicals -> events -> analyst
        -> blended, ranked conviction. Used by both run() and build_plan()."""
        p = self.portal
        p.ensure_built()
        symbols = p.held_symbols or p._all_symbols()

        quotes = p.market.refresh(symbols)
        prices = {s: q.price for s, q in quotes.items()}
        techs = {s: technical_score(s, p.market.history(s)) for s in symbols}

        brief = p.intel.briefing(symbols)
        geo = brief.geo
        sentiment = brief.symbol_sentiment
        geo_bias = geo.get("ticker_bias", {})

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

        # Macro / policy-cycle tilt for today (current administration + active bills).
        from ..macro import MacroEngine
        macro = MacroEngine().symbol_biases(symbols)

        convictions = []
        for s in symbols:
            fc = self.forecaster.predict(s, p.market.history(s))
            inputs = {
                "technical": techs[s].score if techs[s].ready else None,
                "forecast": fc.score() if fc.confidence > 0 else None,
                "analyst": analyst.ratings.get(s),
                "sentiment": sentiment.get(s),
                "macro": macro.get(s) if abs(macro.get(s, 0.0)) > 0.02 else None,
                "geopolitical": geo_bias.get(s),
            }
            conv = self.conviction.blend(s, {k: v for k, v in inputs.items() if v is not None})
            convictions.append(conv)
        convictions = self.conviction.rank(convictions)
        return {"symbols": symbols, "prices": prices, "techs": techs, "brief": brief,
                "geo": geo, "analyst": analyst, "convictions": convictions, "positions": positions}

    def run(self) -> DailyReport:
        p = self.portal
        ctx = self._analyze()
        prices = ctx["prices"]
        geo = ctx["geo"]
        analyst = ctx["analyst"]
        convictions = ctx["convictions"]

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

    def build_plan(self, write: bool = True) -> dict[str, Any]:
        """Produce a TRADE PLAN for execution through the Robinhood MCP.

        Emits order *intents* (not paper fills) with the campaign guardrails and
        the exact limits the executor must honor. Written to data/trade_plan.json
        for the local Claude (with the robinhood-trading MCP) to read, verify
        against the live account, present for approval, and execute.
        """
        from ..models import Side
        p = self.portal
        ctx = self._analyze()
        prices, convictions, positions = ctx["prices"], ctx["convictions"], ctx["positions"]

        camp = p.campaign
        book = p._book_value()
        camp_status = camp.status(book).to_dict() if camp else {}
        halted = camp.breached(book) if camp else False
        focus = set(camp.focus_symbols) if camp else set()
        derisk = camp.derisk_factor() if camp else 1.0
        limits = p.risk.limits

        orders: list[dict] = []
        if not halted and not p.executor.killed:
            for conv in convictions:
                sig = conviction_to_signal(conv.symbol, conv.score, p.market.history(conv.symbol),
                                           params=self.sizing)
                if sig is None:
                    continue
                if sig.side is Side.BUY and focus and conv.symbol not in focus:
                    continue                                  # concentration: buys only in focus names
                sig.order_value = round(sig.order_value * derisk, 2)   # exit-clock scaling
                if sig.order_value <= 0:
                    continue
                px = prices.get(conv.symbol, 0.0)
                held = positions.get(conv.symbol)
                notes: list[str] = []
                if conv.symbol in focus:
                    notes.append("focus name")
                if sig.side is Side.SELL and held is None:
                    notes.append("no shares held — SKIP (verify live account)")
                if sig.side is Side.BUY:
                    held_val = (held.quantity * px) if held else 0.0
                    if held_val + sig.order_value > limits.max_position_value:
                        notes.append(f"would exceed max position ${limits.max_position_value:,.0f} — trim size")
                if sig.order_value > limits.max_order_value:
                    sig.order_value = limits.max_order_value
                    notes.append(f"capped at max order ${limits.max_order_value:,.0f}")
                orders.append({
                    "symbol": conv.symbol, "side": sig.side.value,
                    "order_value": sig.order_value,
                    "est_shares": round(sig.order_value / px, 4) if px else None,
                    "limit_or_market": "market",
                    "conviction": round(conv.score, 3), "action": conv.action,
                    "focus": conv.symbol in focus,
                    "held_shares": held.quantity if held else 0,
                    "rationale": conv.to_dict().get("contributions", {}),
                    "guardrail_notes": notes,
                })

        plan = {
            "generated": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "mode": p.settings.mode.value,
            "campaign": camp_status,
            "guardrails": {
                "drawdown_halt_active": halted,
                "kill_switch": p.executor.killed,
                "phase": camp.phase() if camp else "n/a",
                "derisk_factor": round(derisk, 3),
                "requires_human_approval": True,
            },
            "limits": {
                "max_order_value": limits.max_order_value,
                "max_position_value": limits.max_position_value,
                "max_orders_per_day": limits.max_orders_per_day,
                "max_daily_loss": limits.max_daily_loss,
                "trailing_drawdown_halt_pct": camp_status.get("drawdown_halt_pct"),
                "focus_symbols": sorted(focus),
            },
            "orders": orders,
            "executor_instructions": (
                "Execute ONLY the orders listed, as MARKET orders, through the robinhood-trading MCP. "
                "First read the live account and positions; verify each order against them "
                "(skip any SELL for shares not actually held). Show the user the full plan and get "
                "explicit approval before placing anything. If drawdown_halt_active or kill_switch is "
                "true, place NOTHING. Never exceed the limits above. Report every fill back to the user."
            ),
        }
        if halted:
            plan["executor_instructions"] = ("HALT: campaign drawdown breached. Place NO orders. "
                                             "Preserve capital and alert the user.")

        if write:
            path = ROOT / "data" / "trade_plan.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
            plan["_written_to"] = str(path)
        return plan

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
