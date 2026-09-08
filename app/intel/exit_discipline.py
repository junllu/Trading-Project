"""Exit discipline — measuring what selling actually cost.

Every analysis in this project until now scored ENTRIES. The per-buy study
concluded the operator's selection had no edge versus SPY, and that conclusion
was answering the wrong question, because the money was never lost at the entry.

The record, plainly:

    total realised P&L, 2016-2026, all names      +$70,000
    forgone on THREE early exits, at today's price  -$112,000
        PLTR   sold 643 @ $28.14  -> $174.33   -$94,003
        RKLB   sold 225 @  $5.43  ->  $64.26   -$13,236
        MU     sold   5 @ $80.75  -> $1016.59   -$4,679

The exits cost more than a decade of trading made. And they were not stock
decisions: PLTR and RKLB were sold on the same day as sixteen other names, for
$33,620, with $0 bought. Three such days exist (2021-02-02, 2024-05-22,
2024-07-17), each a portfolio-wide liquidation that caught the good names along
with the bad.

So there are two distinct failure modes and they pull in opposite directions:

    HELD TOO LONG   averaging into a dead theme. Weed: bought until 2024-05,
                    three years past the Feb-2021 peak. Cost ~$3.4k.
    SOLD TOO EARLY  liquidating a live theme with no re-entry rule. Cost ~$112k.

The second is thirty times more expensive, and it is the one no risk system
looks for — every guardrail ever written watches for losses, none watch for
absence. A position you do not hold generates no drawdown, no alert, and no
entry in any log. It is invisible by construction, which is exactly why it needs
a detector of its own.

WHAT THIS DOES

  1. Prices every past exit against what happened next, so the cost of selling
     is a number rather than a feeling.
  2. Flags names sold out of a theme that is STILL ALIVE — the re-entry list.
     "Did not re-enter on time" is not a discipline problem if nothing ever
     tells you the door is still open.
  3. Distinguishes a LIQUIDATION (many names, nothing bought) from a ROTATION
     (sold to fund something else). The 2026-06-09 reallocation was deliberate
     and worked; the three liquidations were reflexes and did not.

    python -m app.intel.exit_discipline
"""
from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass, field

from ..config import ROOT

HISTORY_PATH = ROOT / "data" / "trade_history.json"
PRICES_DIR = ROOT / "data" / "prices"

# A sell day touching this many names with negligible buying is a portfolio
# action, not a set of independent judgements about companies.
LIQUIDATION_MIN_NAMES = 6
LIQUIDATION_MAX_REINVEST = 0.20      # bought / sold


@dataclass
class Exit:
    symbol: str
    date: str
    price: float
    quantity: float
    price_now: float
    peak_since: float
    split_adjusted: bool = False
    raw_price: float = 0.0
    split_factor: float = 1.0

    @property
    def move_pct(self) -> float:
        return (self.price_now / self.price - 1) * 100 if self.price else 0.0

    @property
    def forgone_now(self) -> float:
        return (self.price_now - self.price) * self.quantity

    @property
    def forgone_peak(self) -> float:
        return (self.peak_since - self.price) * self.quantity


@dataclass
class SellDay:
    date: str
    sold: float
    bought: float
    symbols: list[str] = field(default_factory=list)

    @property
    def reinvest_ratio(self) -> float:
        return self.bought / self.sold if self.sold else 0.0

    @property
    def kind(self) -> str:
        if len(self.symbols) >= LIQUIDATION_MIN_NAMES and \
                self.reinvest_ratio <= LIQUIDATION_MAX_REINVEST:
            return "LIQUIDATION"
        if self.bought > 0 and len(self.symbols) >= LIQUIDATION_MIN_NAMES:
            return "ROTATION"
        return "trim"


# A trade price and a split-adjusted close should agree on the trade date. When
# they disagree by more than this, a split happened between then and now.
SPLIT_TOLERANCE = 0.35


def _split_factor(traded_price: float, adjusted_on_date: float) -> float:
    """Ratio to convert a RAW trade price into today's adjusted terms.

    trade_history.json stores prices as executed; the price cache is
    split-adjusted. Comparing the two directly turns NVDA's 10:1 split into a
    fabricated -76% "saving" and GE's 1:8 reverse split into a fabricated
    +2774% "forgone gain" — the two largest errors in the first version of this
    report, both pointing in flattering directions.
    """
    if traded_price <= 0 or adjusted_on_date <= 0:
        return 1.0
    return adjusted_on_date / traded_price


def _series(symbol: str) -> list[tuple[str, float]]:
    p = PRICES_DIR / f"{symbol.upper()}.csv"
    if not p.exists():
        return []
    out = []
    with p.open("r", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                out.append((row["date"], float(row["close"])))
            except (KeyError, ValueError):
                continue
    return out


def _orders() -> list[dict]:
    if not HISTORY_PATH.exists():
        return []
    return json.loads(HISTORY_PATH.read_text(encoding="utf-8")).get("orders", [])


def price_the_exits(min_forgone: float = 500.0) -> list[Exit]:
    """What did each name do AFTER it was sold? Aggregated to the last exit."""
    orders = _orders()
    held_now = _currently_held()
    last_sell: dict[str, dict] = {}
    qty: dict[str, float] = {}
    for o in orders:
        if o.get("side") != "sell":
            continue
        s = str(o["symbol"]).upper()
        qty[s] = qty.get(s, 0.0) + float(o["quantity"])
        if s not in last_sell or o["date"] >= last_sell[s]["date"]:
            last_sell[s] = o

    out: list[Exit] = []
    for s, o in last_sell.items():
        if s in held_now:
            continue                      # still held: not an exit
        series = _series(s)
        if not series:
            continue
        after = [(d, c) for d, c in series if d >= o["date"]]
        if len(after) < 2:
            continue

        # Restate the trade into today's adjusted terms. Both price AND
        # quantity move under a split, and they move inversely — so the
        # proceeds are invariant and only the per-share comparison needs it.
        traded = float(o["price"])
        adj_on_date = after[0][1]
        f = _split_factor(traded, adj_on_date)
        split_adjusted = abs(f - 1.0) > SPLIT_TOLERANCE
        price = traded * f if split_adjusted else traded
        quantity = qty[s] / f if split_adjusted else qty[s]

        e = Exit(s, o["date"], price, quantity, series[-1][1],
                 max(c for _, c in after), split_adjusted=split_adjusted,
                 raw_price=traded, split_factor=round(f, 4))
        if abs(e.forgone_now) >= min_forgone:
            out.append(e)
    return sorted(out, key=lambda e: -e.forgone_now)


def _currently_held() -> set[str]:
    try:
        from ..portfolio.holdings import load_holdings
        return {str(h["symbol"]).upper() for h in load_holdings()}
    except Exception:
        return set()


def sell_days() -> list[SellDay]:
    by_date: dict[str, SellDay] = {}
    for o in _orders():
        d = by_date.setdefault(o["date"], SellDay(date=o["date"], sold=0.0, bought=0.0))
        v = float(o["quantity"]) * float(o["price"])
        if o.get("side") == "sell":
            d.sold += v
            if o["symbol"] not in d.symbols:
                d.symbols.append(str(o["symbol"]).upper())
        else:
            d.bought += v
    return sorted((d for d in by_date.values() if d.sold > 0),
                  key=lambda d: -d.sold)


def reentry_candidates() -> list[dict]:
    """Names sold out of a theme that is STILL ALIVE.

    This is the detector that would have caught PLTR and RKLB. A sold position
    generates no drawdown and no alert — its absence is silent — so unless
    something actively looks for it, "did not re-enter on time" is guaranteed
    rather than blameworthy.
    """
    try:
        from .themes import ALIVE, THEMES, report as themes_report
    except Exception:
        return []
    live = {n for n, s in themes_report()["themes"].items() if s["state"] == ALIVE}
    member_of = {m: n for n, ms in THEMES.items() for m in ms}
    held = _currently_held()

    out = []
    for e in price_the_exits(min_forgone=0.0):
        theme = member_of.get(e.symbol)
        if theme in live and e.symbol not in held:
            out.append({"symbol": e.symbol, "theme": theme, "sold_on": e.date,
                        "sold_at": round(e.price, 2), "now": round(e.price_now, 2),
                        "move_pct": round(e.move_pct, 1),
                        "forgone_now": round(e.forgone_now)})
    return sorted(out, key=lambda r: -r["forgone_now"])


def report() -> dict:
    exits = price_the_exits()
    liq = [d for d in sell_days() if d.kind == "LIQUIDATION"]
    return {
        "exits_priced": len(exits),
        "total_forgone_now": round(sum(e.forgone_now for e in exits if e.forgone_now > 0)),
        "worst": [{"symbol": e.symbol, "date": e.date, "forgone_now": round(e.forgone_now)}
                  for e in exits[:5]],
        "liquidation_days": [{"date": d.date, "sold": round(d.sold),
                              "names": len(d.symbols)} for d in liq[:5]],
        "reentry_candidates": reentry_candidates()[:10],
        "note": ("Forgone gains are invisible to every risk system: a position you do "
                 "not hold produces no drawdown, no alert and no log entry."),
    }


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    if args.json:
        print(json.dumps(report(), indent=2))
        return

    exits = price_the_exits()
    print("=" * 78)
    print("  EXIT DISCIPLINE — what selling actually cost")
    print("=" * 78)
    print(f"\n  {'symbol':7} {'sold':>11} {'at':>10} {'now':>10} {'move':>9} "
          f"{'forgone now':>13}")
    print("  " + "-" * 70)
    for e in exits[:10]:
        tag = f"  [split {e.split_factor:g}x, raw ${e.raw_price:.2f}]" if e.split_adjusted else ""
        print(f"  {e.symbol:7} {e.date:>11} {e.price:>10.2f} {e.price_now:>10.2f} "
              f"{e.move_pct:>+8.0f}% {e.forgone_now:>+13,.0f}{tag}")

    pos = sum(e.forgone_now for e in exits if e.forgone_now > 0)
    neg = sum(e.forgone_now for e in exits if e.forgone_now < 0)
    print(f"\n  forgone by selling winners early : {pos:>+12,.0f}")
    print(f"  saved by selling losers          : {neg:>+12,.0f}")
    print(f"  NET cost of exit timing          : {pos + neg:>+12,.0f}")

    print(f"\n  {'=' * 74}")
    print("  SELL DAYS — a portfolio action is not a set of stock decisions")
    for d in sell_days()[:6]:
        print(f"    {d.date}  {d.kind:12} sold ${d.sold:>9,.0f}  "
              f"reinvested {d.reinvest_ratio:>5.0%}  across {len(d.symbols):>2} names")

    rc = reentry_candidates()
    if rc:
        print(f"\n  {'=' * 74}")
        print("  RE-ENTRY CANDIDATES — sold, but the theme is still ALIVE")
        for r in rc[:8]:
            print(f"    {r['symbol']:6} {r['theme']:10} sold {r['sold_on']} @ "
                  f"${r['sold_at']:>8.2f}  now ${r['now']:>8.2f}  "
                  f"({r['move_pct']:+.0f}%)  forgone ${r['forgone_now']:>+10,.0f}")
        print("\n    This list is the point. A sold position raises no alarm anywhere,")
        print("    so its absence has to be looked for deliberately or not at all.")


if __name__ == "__main__":
    _main()
