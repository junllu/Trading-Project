"""Curated-source feed — dated views from named analysts, point-in-time honest.

This is the input path for the conviction engine's per-source slots (`serenity`,
`professor_jiang`, `personal`). Unlike the cashtag buzz in xfeed.py, these are
*attributed, dated opinions* from specific people you follow.

The load-bearing design rule: **every view records when it was published, and
every query filters by `as_of`.** A backtest replaying 2024-03-01 can only see
views published on or before that date. This is deliberate — the macro timeline
(app/macro/timeline.py) was hand-authored with hindsight and silently leaked
future knowledge into backtests; this store cannot do that.

Views decay with age (exponential, `HALFLIFE_DAYS`) so a stale call fades rather
than counting forever, and a source whose newest view is older than
`STALE_AFTER_DAYS` is reported stale so a dead feed can't masquerade as signal.

    python -m app.intel.feeds list
    python -m app.intel.feeds add --source professor_jiang --symbol MU \\
        --bias 0.6 --published 2026-09-02 --rationale "HBM supply tightness"
    python -m app.intel.feeds status
"""
from __future__ import annotations

import argparse
import json
import math
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from ..config import ROOT

FEED_PATH = ROOT / "data" / "feeds" / "source_views.jsonl"

# Conviction-engine source slots this store can feed.
KNOWN_SOURCES = ("serenity", "professor_jiang", "personal")

HALFLIFE_DAYS = 30.0        # a view's weight halves every 30 days
STALE_AFTER_DAYS = 21       # bi-weekly cadence -> older than 3 weeks is stale

# How long a source's view must AGE before it is allowed into the blend.
# Derived from measured hit rates (python -m app.intel.score_source), never
# guessed: a source that is reliably early is valuable BECAUSE it is early, and
# the fix is to wait for it rather than to weight it down or drop it.
#
#   serenity   25% hit at 30d -> 43.8% at 90d -> 56.2% to date (n=16).
#              90 is the first horizon where she is better than a coin flip
#              would be after costs. Re-derive as n grows; 16 is a reading.
#
# A source with no entry here ripens instantly (0), which is the old behaviour.
# Adding a source without measuring it first means trusting it by default — the
# opposite of what the scorer exists for.
SOURCE_RIPEN_DAYS: dict[str, int] = {
    "serenity": 90,
}


def _parse_day(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


@dataclass
class SourceView:
    """One dated, attributed opinion about one symbol."""
    source: str                       # must be in KNOWN_SOURCES
    symbol: str
    bias: float                       # -1..1 (bearish..bullish)
    published: str                    # YYYY-MM-DD — when the SOURCE said it
    confidence: float = 0.5           # 0..1, how firm the call was
    rationale: str = ""
    url: str = ""
    ingested_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"source": self.source, "symbol": self.symbol, "bias": round(self.bias, 3),
                "published": self.published, "confidence": round(self.confidence, 3),
                "rationale": self.rationale, "url": self.url, "ingested_at": self.ingested_at}

    @staticmethod
    def from_dict(d: dict) -> "SourceView":
        return SourceView(
            source=str(d["source"]), symbol=str(d["symbol"]).upper(),
            bias=float(d["bias"]), published=str(d["published"]),
            confidence=float(d.get("confidence", 0.5)), rationale=str(d.get("rationale", "")),
            url=str(d.get("url", "")), ingested_at=float(d.get("ingested_at", 0.0)),
        )


class FeedStore:
    """Append-only JSONL store of dated source views."""

    def __init__(self, path: Path | None = None):
        self.path = path or FEED_PATH

    def add(self, view: SourceView) -> None:
        if view.source not in KNOWN_SOURCES:
            raise ValueError(f"unknown source '{view.source}'. Known: {KNOWN_SOURCES}")
        _parse_day(view.published)                      # validate date format early
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(view.to_dict()) + "\n")

    def all_views(self) -> list[SourceView]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(SourceView.from_dict(json.loads(line)))
        return out

    def views(self, source: str | None = None, symbol: str | None = None,
              as_of: str | date | None = None) -> list[SourceView]:
        """Views matching the filters, published ON OR BEFORE `as_of`.

        `as_of` is the point-in-time guard: omit it only for live "what do we
        know today" reads, never when replaying history.
        """
        cutoff = _parse_day(as_of) if isinstance(as_of, str) else as_of
        out = []
        for v in self.all_views():
            if source and v.source != source:
                continue
            if symbol and v.symbol != symbol.upper():
                continue
            if cutoff and _parse_day(v.published) > cutoff:
                continue                                 # future view — invisible at `as_of`
            out.append(v)
        return sorted(out, key=lambda v: v.published)

    def bias(self, source: str, symbol: str, as_of: str | date | None = None) -> float | None:
        """Consensus bias in [-1, 1], RIPENED then age-decayed.

        Plain age-decay was actively wrong for the only source we have measured.
        app/intel/score_source.py scored Serenity's 16 dated views and found
        accuracy rising monotonically with time:

            30 days   25.0% hit, mean  -9.5%
            90 days   43.8% hit, mean -10.4%
            to date   56.2% hit, mean  +4.6%

        She is early and directionally right, which is a real and useful
        property — but decay weighted her most heavily in the first month, the
        window where acting on her LOST money. The blend was amplifying the
        worst part of a good signal.

        So a view now RIPENS before it counts (SOURCE_RIPEN_DAYS), and only then
        decays. Silence still returns None rather than 0.0.

        The ripen period is measured, not assumed, and must be re-derived as n
        grows — 16 views is not evidence, it is a first reading.
        """
        rows = self.views(source=source, symbol=symbol, as_of=as_of)
        if not rows:
            return None
        ref = _parse_day(as_of) if isinstance(as_of, str) else (as_of or date.today())
        ripen = SOURCE_RIPEN_DAYS.get(source, 0)
        num = den = 0.0
        for v in rows:
            age = max(0, (ref - _parse_day(v.published)).days)
            if age < ripen:
                continue                      # too fresh to be trustworthy yet
            # Decay measured from the END of ripening, so a ripened view starts
            # at full weight instead of arriving already half-decayed.
            decay = math.exp(-math.log(2) * (age - ripen) / HALFLIFE_DAYS)
            w = max(0.0, min(1.0, v.confidence)) * decay
            num += max(-1.0, min(1.0, v.bias)) * w
            den += w
        return max(-1.0, min(1.0, num / den)) if den > 0 else None

    def biases(self, source: str, symbols: list[str], as_of: str | date | None = None) -> dict[str, float]:
        """bias() across many symbols; omits symbols the source hasn't covered."""
        out = {}
        for s in symbols:
            b = self.bias(source, s, as_of)
            if b is not None:
                out[s.upper()] = b
        return out

    def status(self, as_of: str | date | None = None) -> dict:
        """Per-source freshness — surfaces a dead feed instead of hiding it."""
        ref = _parse_day(as_of) if isinstance(as_of, str) else (as_of or date.today())
        out: dict[str, dict] = {}
        for src in KNOWN_SOURCES:
            rows = self.views(source=src, as_of=ref)
            if not rows:
                out[src] = {"views": 0, "newest": None, "age_days": None, "stale": True}
                continue
            newest = rows[-1].published
            age = (ref - _parse_day(newest)).days
            out[src] = {"views": len(rows), "newest": newest, "age_days": age,
                        "stale": age > STALE_AFTER_DAYS,
                        "symbols": sorted({v.symbol for v in rows})}
        return out


def report() -> dict:
    """Agent entrypoint — freshness per source, and which are effectively dead.

    Staleness is the finding here, not an error state. A source that stopped
    publishing three weeks ago still has views on disk, and a blend that keeps
    weighting them is treating an old opinion as a current one.
    """
    st = FeedStore().status()
    live = {k: v for k, v in st.items() if not v["stale"]}
    return {"sources": st,
            "live": sorted(live),
            "stale": sorted(k for k, v in st.items() if v["stale"]),
            "stale_after_days": STALE_AFTER_DAYS,
            "note": ("A stale source contributes nothing to the blend — bias() returns "
                     "None rather than 0.0, because silence is not neutrality.")}


def _main() -> None:
    ap = argparse.ArgumentParser(description="Curated-source view feed.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="record one dated view")
    p_add.add_argument("--source", required=True, choices=KNOWN_SOURCES)
    p_add.add_argument("--symbol", required=True)
    p_add.add_argument("--bias", required=True, type=float, help="-1..1")
    p_add.add_argument("--published", required=True, help="YYYY-MM-DD (when the source said it)")
    p_add.add_argument("--confidence", type=float, default=0.5)
    p_add.add_argument("--rationale", default="")
    p_add.add_argument("--url", default="")

    p_list = sub.add_parser("list", help="list stored views")
    p_list.add_argument("--source", choices=KNOWN_SOURCES)
    p_list.add_argument("--symbol")
    p_list.add_argument("--as-of")

    sub.add_parser("status", help="per-source freshness")
    args = ap.parse_args()
    store = FeedStore()

    if args.cmd == "add":
        store.add(SourceView(source=args.source, symbol=args.symbol.upper(), bias=args.bias,
                             published=args.published, confidence=args.confidence,
                             rationale=args.rationale, url=args.url))
        print(f"added {args.source} {args.symbol.upper()} bias={args.bias:+.2f} ({args.published})")
    elif args.cmd == "list":
        rows = store.views(args.source, args.symbol, args.as_of)
        if not rows:
            print("no views stored")
        for v in rows:
            print(f"{v.published}  {v.source:16} {v.symbol:6} bias={v.bias:+.2f} "
                  f"conf={v.confidence:.2f}  {v.rationale[:60]}")
    elif args.cmd == "status":
        for src, st in store.status().items():
            flag = "STALE" if st["stale"] else "fresh"
            print(f"{src:16} views={st['views']:3}  newest={st['newest']}  "
                  f"age={st['age_days']}d  [{flag}]")


if __name__ == "__main__":
    _main()
