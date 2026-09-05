"""Our own geopolitical analysis system.

A transparent, rules-based engine — NOT a black box. It reads an event, matches
it against a library of geopolitical "themes" (conflict, sanctions, energy
shocks, central-bank action, elections, trade war), and outputs:

  * a risk tilt        (risk-on / risk-off / neutral)
  * affected sectors   with a directional bias (+/-)
  * concrete ticker ideas (ETF/sector proxies) with a bullish/bearish lean
  * a rationale        (which theme fired and why)

You can read every decision it makes, tune the weights, and add themes. That
auditability is the whole point: geopolitics is noisy, so the system should
show its work rather than assert conclusions.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .events import Event, EventCategory


@dataclass
class ThemeMatch:
    theme: str
    risk_tilt: str                       # "risk_off" | "risk_on" | "neutral"
    rationale: str
    sector_bias: dict[str, float]        # sector -> -1..1 (bearish..bullish)
    ticker_bias: dict[str, float]        # ticker -> -1..1
    weight: float = 1.0


# --- theme library --------------------------------------------------------
# Each theme: trigger keywords -> market interpretation. Sector/ticker biases
# are illustrative proxies, not recommendations. Tune to your own thesis.
THEMES: list[dict] = [
    {
        "theme": "armed_conflict",
        "keywords": ["war", "invasion", "missile", "strike", "military", "troops", "escalation", "attack"],
        "risk_tilt": "risk_off",
        "rationale": "Armed conflict → flight to safety; defense + energy + gold bid, broad equities pressured.",
        "sector_bias": {"defense": 0.8, "energy": 0.6, "gold": 0.7, "airlines": -0.6, "broad_equity": -0.5},
        "ticker_bias": {"ITA": 0.8, "XLE": 0.6, "GLD": 0.7, "JETS": -0.6, "SPY": -0.4},
    },
    {
        "theme": "sanctions",
        "keywords": ["sanction", "sanctions", "embargo", "export ban", "blacklist", "seized assets"],
        # Don't fire bearish if the news is actually a rollback (handled by de_escalation).
        "exclude_keywords": ["lifted", "removed", "eased", "suspended", "waived"],
        "risk_tilt": "risk_off",
        "rationale": "Sanctions disrupt supply chains and commodities; energy/defense up, exposed multinationals down.",
        "sector_bias": {"energy": 0.5, "defense": 0.4, "commodities": 0.5, "broad_equity": -0.3},
        "ticker_bias": {"XLE": 0.5, "GLD": 0.4, "SPY": -0.3},
    },
    {
        "theme": "energy_shock",
        "keywords": ["opec", "oil", "crude", "pipeline", "gas supply", "refinery", "barrel", "energy crisis"],
        "risk_tilt": "risk_off",
        "rationale": "Energy supply shock lifts oil; energy producers benefit, transports/consumer squeezed.",
        "sector_bias": {"energy": 0.8, "airlines": -0.7, "consumer": -0.4},
        "ticker_bias": {"XLE": 0.8, "USO": 0.7, "JETS": -0.7},
    },
    {
        "theme": "central_bank",
        "keywords": ["rate hike", "rate cut", "federal reserve", "fed", "ecb", "hawkish", "dovish", "basis points", "tightening"],
        "risk_tilt": "neutral",
        "rationale": "Central-bank action reprices duration; growth/tech sensitive to rates, banks to the curve.",
        "sector_bias": {"tech": -0.3, "banks": 0.3, "bonds": -0.3, "gold": 0.2},
        "ticker_bias": {"QQQ": -0.3, "XLF": 0.3, "TLT": -0.3},
    },
    {
        "theme": "trade_war",
        "keywords": ["tariff", "tariffs", "trade war", "trade deal", "import duty", "supply chain", "decoupling"],
        "risk_tilt": "risk_off",
        "rationale": "Tariffs raise input costs and hit exporters/semis; domestic-focused names relatively insulated.",
        "sector_bias": {"semiconductors": -0.6, "industrials": -0.4, "domestic_small_cap": 0.2},
        "ticker_bias": {"SMH": -0.6, "XLI": -0.4, "IWM": 0.2},
    },
    {
        "theme": "election_stability",
        "keywords": ["election", "coup", "regime", "referendum", "government shutdown", "impeachment", "protests"],
        "risk_tilt": "risk_off",
        "rationale": "Political-stability shocks raise the risk premium; volatility bid, broad equities softer.",
        "sector_bias": {"volatility": 0.6, "gold": 0.4, "broad_equity": -0.3},
        "ticker_bias": {"VIXY": 0.6, "GLD": 0.4, "SPY": -0.3},
    },
    {
        "theme": "de_escalation",
        "keywords": ["ceasefire", "peace deal", "truce", "de-escalation", "sanctions lifted", "agreement reached"],
        "risk_tilt": "risk_on",
        "rationale": "De-escalation releases the risk premium; equities/airlines rally, safe havens unwind.",
        "sector_bias": {"broad_equity": 0.5, "airlines": 0.6, "gold": -0.4, "energy": -0.3},
        "ticker_bias": {"SPY": 0.5, "JETS": 0.6, "GLD": -0.4},
    },
]


@dataclass
class GeoAssessment:
    risk_tilt: str = "neutral"
    confidence: float = 0.0                     # 0..1
    matched_themes: list[str] = field(default_factory=list)
    sector_bias: dict[str, float] = field(default_factory=dict)
    ticker_bias: dict[str, float] = field(default_factory=dict)
    rationale: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "risk_tilt": self.risk_tilt,
            "confidence": round(self.confidence, 2),
            "matched_themes": self.matched_themes,
            "sector_bias": {k: round(v, 2) for k, v in self.sector_bias.items()},
            "ticker_bias": {k: round(v, 2) for k, v in sorted(self.ticker_bias.items(), key=lambda x: -abs(x[1]))},
            "rationale": self.rationale,
        }


class GeopoliticalEngine:
    def __init__(self, themes: list[dict] | None = None):
        self.themes = themes or THEMES

    def match_event(self, event: Event) -> list[ThemeMatch]:
        text = f"{event.headline} {event.body}".lower()
        matches: list[ThemeMatch] = []
        for t in self.themes:
            hit_words = [k for k in t["keywords"] if k in text]
            if not hit_words:
                continue
            # Skip a theme when a negation/rollback keyword is present.
            if any(x in text for x in t.get("exclude_keywords", [])):
                continue
            # weight scales with how many trigger words fired and event importance
            weight = min(1.0, 0.4 + 0.2 * len(hit_words)) * (0.5 + 0.5 * event.importance)
            matches.append(ThemeMatch(
                theme=t["theme"], risk_tilt=t["risk_tilt"],
                rationale=f"{t['rationale']} (matched: {', '.join(hit_words)})",
                sector_bias=t["sector_bias"], ticker_bias=t["ticker_bias"], weight=weight,
            ))
        return matches

    def assess(self, events: list[Event]) -> GeoAssessment:
        """Aggregate all geopolitical/energy events into one weighted view."""
        relevant = [e for e in events if e.category in (EventCategory.GEOPOLITICAL, EventCategory.ENERGY, EventCategory.MACRO)]
        agg = GeoAssessment()
        tilt_score = 0.0
        total_w = 0.0
        sector_acc: dict[str, float] = {}
        ticker_acc: dict[str, float] = {}

        for event in relevant:
            for m in self.match_event(event):
                if m.theme not in agg.matched_themes:
                    agg.matched_themes.append(m.theme)
                    agg.rationale.append(f"[{m.theme}] {m.rationale}")
                sign = {"risk_off": -1.0, "risk_on": 1.0, "neutral": 0.0}[m.risk_tilt]
                tilt_score += sign * m.weight
                total_w += m.weight
                for sec, b in m.sector_bias.items():
                    sector_acc[sec] = sector_acc.get(sec, 0.0) + b * m.weight
                for tk, b in m.ticker_bias.items():
                    ticker_acc[tk] = ticker_acc.get(tk, 0.0) + b * m.weight

        if total_w > 0:
            norm = tilt_score / total_w
            agg.risk_tilt = "risk_off" if norm < -0.15 else "risk_on" if norm > 0.15 else "neutral"
            agg.confidence = min(1.0, total_w / 3.0)
            agg.sector_bias = {k: max(-1, min(1, v / total_w)) for k, v in sector_acc.items()}
            agg.ticker_bias = {k: max(-1, min(1, v / total_w)) for k, v in ticker_acc.items()}
        return agg
