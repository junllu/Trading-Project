"""Claude (Opus) as the analytic brain.

Replaces Grok for reasoning: given each symbol's technicals, position context,
and any events, Claude returns a per-symbol rating in [-1, 1] with a one-line
rationale, plus a short portfolio-level note. Uses the official Anthropic SDK.

Unlike Grok's live search, Claude here reasons over the data we provide — it
does not browse. Supply events (from any source) and it will factor them in.

No credentials?  It degrades to a transparent heuristic (technical-driven)
rating so the pipeline runs and is testable offline. Note: unlike xAI, the
Anthropic API host is reachable from this environment, so a real
ANTHROPIC_API_KEY produces live Claude analysis here and locally alike.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

# Default to the most capable Opus per current Anthropic guidance; override via env.
LLM_MODEL = os.getenv("PORTAL_LLM_MODEL", "claude-opus-5")

SYSTEM_PROMPT = """You are a disciplined equity analyst assisting a retail trader.
You are given, per symbol: current price, a composite technical score in [-1,1],
recent indicators, the trader's position (shares and cost), and any relevant
events. Rate each symbol's 2–6 week outlook.

Respond ONLY with a JSON object:
{
  "ratings": [
    {"symbol": str, "rating": float in [-1,1], "rationale": str (<=140 chars),
     "risk": one of ["low","medium","high"]}
  ],
  "portfolio_note": str (<=300 chars: concentration, risk, what to watch)
}
Be decisive but calibrated. A rating near 0 means genuinely neutral. No prose
outside the JSON, no markdown fences."""


@dataclass
class AnalystResult:
    ratings: dict[str, float] = field(default_factory=dict)         # symbol -> rating
    rationales: dict[str, str] = field(default_factory=dict)
    risk: dict[str, str] = field(default_factory=dict)
    portfolio_note: str = ""
    source: str = "heuristic"                                       # "claude" | "heuristic"

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "portfolio_note": self.portfolio_note,
            "ratings": {k: round(v, 3) for k, v in self.ratings.items()},
            "rationales": self.rationales,
            "risk": self.risk,
        }


class ClaudeAnalyst:
    def __init__(self, api_key: str | None = None, model: str = LLM_MODEL):
        # None => read env; an explicit string (incl. "") is an override that
        # forces heuristic mode. The SDK resolves profiles at call time in live mode.
        self.api_key = os.getenv("ANTHROPIC_API_KEY", "") if api_key is None else api_key
        self.model = model
        self._client = None

    @property
    def live(self) -> bool:
        return bool(self.api_key)

    # --- public API --------------------------------------------------------
    def analyze(self, symbol_data: list[dict[str, Any]]) -> AnalystResult:
        """symbol_data: [{symbol, price, technical, rsi, position_shares,
        avg_cost, unrealized_pct, events:[headlines]}]."""
        if self.live:
            try:
                return self._analyze_live(symbol_data)
            except Exception:
                pass  # never let the analyst break the loop
        return self._analyze_heuristic(symbol_data)

    # --- live (Claude) -----------------------------------------------------
    def _get_client(self):
        if self._client is None:
            import anthropic
            self._client = anthropic.Anthropic()  # resolves key/profile from env
        return self._client

    def _analyze_live(self, symbol_data: list[dict[str, Any]]) -> AnalystResult:
        client = self._get_client()
        user = json.dumps({"symbols": symbol_data}, default=str)
        resp = client.messages.create(
            model=self.model,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        return self._parse(text, source="claude")

    def _parse(self, text: str, source: str) -> AnalystResult:
        t = text.strip()
        if t.startswith("```"):
            t = t.strip("`")
            t = t[t.find("{"):]
        start, end = t.find("{"), t.rfind("}")
        data = json.loads(t[start:end + 1]) if start != -1 else {}
        res = AnalystResult(source=source, portfolio_note=str(data.get("portfolio_note", "")))
        for r in data.get("ratings", []):
            sym = str(r.get("symbol", "")).upper()
            if not sym:
                continue
            res.ratings[sym] = max(-1.0, min(1.0, float(r.get("rating", 0.0))))
            res.rationales[sym] = str(r.get("rationale", ""))
            res.risk[sym] = str(r.get("risk", "medium"))
        return res

    # --- heuristic fallback ------------------------------------------------
    def _analyze_heuristic(self, symbol_data: list[dict[str, Any]]) -> AnalystResult:
        res = AnalystResult(source="heuristic")
        overbought = 0
        for d in symbol_data:
            sym = str(d.get("symbol", "")).upper()
            tech = float(d.get("technical", 0.0) or 0.0)
            r = d.get("rsi")
            rating = tech
            note = f"technical {tech:+.2f}"
            if r is not None:
                if r >= 75:
                    rating -= 0.2
                    note += f", RSI {r:.0f} stretched"
                    overbought += 1
                elif r <= 25:
                    rating += 0.15
                    note += f", RSI {r:.0f} washed out"
            res.ratings[sym] = max(-1.0, min(1.0, rating))
            res.rationales[sym] = note
            res.risk[sym] = "high" if abs(tech) > 0.6 else "medium" if abs(tech) > 0.3 else "low"
        res.portfolio_note = (
            "Heuristic mode (no ANTHROPIC_API_KEY). Ratings are technical-driven; "
            f"{overbought} name(s) look overbought. Add a key for Claude's reasoned view."
        )
        return res
