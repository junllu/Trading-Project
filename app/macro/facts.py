"""Dated structural facts — supply constraints, capacity, policy.

The third kind of macro input, and the one the old timeline conflated with the
others. These are not price signals and must not be traded as such. They are
*selection* inputs: durable, physical or contractual conditions that change
which names are worth holding on a quarters-to-years horizon.

The distinction that matters:

    signal  "semis are bullish right now"        -> unfalsifiable, hindsight-prone
    fact    "30% of semiconductor-grade helium   -> dated, sourced, expires,
             offline since 2026-02, no             checkable against reality
             substitute, effects to 2029"

Every fact carries `known_from` — the date it became public knowledge. Queries
filter on it, so a backtest replaying 2025 cannot see a 2026 supply shock. This
is the same discipline as app/intel/feeds.py, applied to macro.

Facts also carry `expires`: a constraint that resolves is no longer a reason to
hold. Nothing here decays into permanent bullishness.

    python -m app.macro.facts
    python -m app.macro.facts --as-of 2026-01-01     # replay: shock invisible
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..config import ROOT

FACTS_PATH = ROOT / "data" / "macro_facts.jsonl"


@dataclass
class MacroFact:
    key: str
    headline: str
    known_from: str                  # YYYY-MM-DD — when this became public
    expires: str                     # YYYY-MM-DD — when the constraint resolves
    category: str                    # supply | demand | policy | capacity
    direction: float                 # -1..1 effect on the AFFECTED names
    affected_sectors: list[str] = field(default_factory=list)
    affected_symbols: list[str] = field(default_factory=list)
    confidence: float = 0.5
    source: str = ""
    note: str = ""

    def active_on(self, d: date) -> bool:
        return _day(self.known_from) <= d <= _day(self.expires)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


def _day(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


# --- seed set, from the 2026-09-06 research pass ---------------------------
# Each entry is checkable against its source. Add to this rather than editing
# history: a fact whose premise breaks should get a corrected `expires`, not a
# silent rewrite, or the look-ahead problem returns by the back door.
SEED_FACTS: list[MacroFact] = [
    MacroFact(
        key="helium_ras_laffan",
        headline="~30% of semiconductor-grade helium offline after strikes on Qatar's Ras Laffan",
        known_from="2026-03-01", expires="2029-12-31",
        category="supply", direction=0.45,
        affected_sectors=["semiconductors", "ai_infra"],
        affected_symbols=["MU", "SNDK", "WDC", "STX", "TSM"],
        confidence=0.7,
        source="CNBC / Carnegie / Bloomberg, Mar 2026",
        note=("Helium has no substitute in lithography and thermal transfer. Spot "
              "+40-100%, force majeure declared. Physical damage means effects run "
              "to 2029+ — a ceasefire does not resolve it. Constrains SUPPLY, so it "
              "supports incumbent producer pricing power."),
    ),
    MacroFact(
        key="hormuz_energy_korea",
        headline="Strait of Hormuz pressure raises Korean fab energy costs",
        known_from="2026-03-01", expires="2027-12-31",
        category="supply", direction=0.25,
        affected_sectors=["semiconductors"],
        affected_symbols=["MU", "SNDK"],
        confidence=0.5,
        source="Carnegie Endowment, Mar 2026",
        note="South Korea imports >70% of crude via Hormuz; Korea is the memory hub.",
    ),
    MacroFact(
        key="memory_sold_out_2027",
        headline="Samsung/SK Hynix/Micron 2027 DRAM and HBM capacity reported fully booked",
        known_from="2026-08-09", expires="2027-12-31",
        category="capacity", direction=0.55,
        affected_sectors=["semiconductors"],
        affected_symbols=["MU", "SNDK", "WDC", "STX"],
        confidence=0.6,
        source="TweakTown / Seeking Alpha, Aug 2026; Samsung Jul 30 call",
        note=("Companies have NOT formally confirmed. Samsung guided tight supply "
              "THROUGH 2028, worst in 2027 — 2028 is tight, not booked. Capacity "
              "relief arriving ~2028 is precisely the campaign's exit thesis."),
    ),
    MacroFact(
        key="china_domestic_substitution",
        headline="China AI-chip demand shifting to domestic silicon; NVDA share forecast 40% -> 8%",
        known_from="2026-06-01", expires="2028-12-31",
        category="demand", direction=-0.40,
        affected_sectors=["semiconductors"],
        affected_symbols=["NVDA", "AMD"],
        confidence=0.6,
        source="Bernstein via MarketScale; Brookings; Tom's Hardware, 2026",
        note=("Huawei Ascend 950 ~ H200 comparable. Substitution is structural: "
              "procurement confidence 'is difficult to reverse even if export "
              "restrictions ease'. A diplomatic thaw may move sentiment without "
              "restoring revenue — do not read a summit as a fundamental reversal."),
    ),
]


def load_facts(path: Path | None = None) -> list[MacroFact]:
    p = path or FACTS_PATH
    facts = list(SEED_FACTS)
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    facts.append(MacroFact(**json.loads(line)))
                except Exception:
                    continue
    return facts


def add_fact(fact: MacroFact, path: Path | None = None) -> None:
    p = path or FACTS_PATH
    _day(fact.known_from), _day(fact.expires)          # validate early
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(fact.to_dict()) + "\n")


def facts_as_of(d: date | str | None = None, path: Path | None = None) -> list[MacroFact]:
    """Only facts publicly known on `d` and not yet expired."""
    ref = _day(d) if isinstance(d, str) else (d or date.today())
    return [f for f in load_facts(path) if f.active_on(ref)]


def symbol_bias(symbol: str, d: date | str | None = None,
                path: Path | None = None) -> float:
    """Confidence-weighted structural bias for one symbol. 0.0 when uncovered."""
    sym = symbol.upper()
    num = den = 0.0
    for f in facts_as_of(d, path):
        if sym in {s.upper() for s in f.affected_symbols}:
            num += f.direction * f.confidence
            den += f.confidence
    return max(-1.0, min(1.0, num / den)) if den else 0.0


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=None, help="YYYY-MM-DD; demonstrates point-in-time")
    ap.add_argument("--symbol", default=None)
    args = ap.parse_args()

    ref = args.as_of or date.today().isoformat()
    active = facts_as_of(ref)
    print(f"structural facts known on {ref}: {len(active)} of {len(load_facts())}\n")
    for f in active:
        print(f"  [{f.category:8}] {f.headline}")
        print(f"     dir {f.direction:+.2f} conf {f.confidence:.1f} · known {f.known_from} "
              f"-> expires {f.expires}")
        print(f"     affects: {', '.join(f.affected_symbols) or '—'}   src: {f.source}")
        print()
    if args.symbol:
        print(f"{args.symbol.upper()} structural bias on {ref}: "
              f"{symbol_bias(args.symbol, ref):+.3f}")


if __name__ == "__main__":
    _main()
