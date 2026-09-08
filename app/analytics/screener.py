"""Candidate screener — which tickers are even suitable for the technical strategies.

The config strategies (sma_crossover, rsi_reversion) are trend/mean-reversion
rules. They are not universally applicable: a crossover rule on an illiquid or
barely-moving name produces noise, and on a 100%-vol name it produces ruin. So
this screens for *tradability* first, and thesis relevance second.

Filters, and why each exists:

  liquidity   avg 20d dollar volume >= min_dollar_volume
              Thin names mean slippage the backtest never modelled.
  price       >= min_price
              Sub-$5 tickers have wide spreads and split/reverse-split noise.
  volatility  min_vol <= annualized <= max_vol
              Too low and a crossover never triggers; too high and fixed sizing
              takes far more risk than intended. Note the sizing vol-scalar
              floors at 0.4, so beyond ~75% vol it can no longer shrink enough.
  trend       persistence = share of days closing above the 50-day SMA
              sma_crossover needs names that actually sustain direction.

Nothing here predicts returns — it only rejects symbols where the strategies'
assumptions are broken. That distinction matters given the walk-forward result.

    python -m app.analytics.screener
    python -m app.analytics.screener --min-dollar-volume 50e6 --json
"""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field


@dataclass
class ScreenCriteria:
    min_dollar_volume: float = 25_000_000.0     # 20d average
    min_price: float = 5.0
    min_vol: float = 0.20                       # annualized
    max_vol: float = 0.75                       # above this the vol-scalar floors out
    min_trend_persistence: float = 0.35         # share of days above the 50d SMA
    max_trend_persistence: float = 0.95         # ~1.0 means it never pulls back
    label: str = "tactical"

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    # --- pool profiles -----------------------------------------------------
    # The two pools do different jobs, so they need different screens. Applying
    # one screen to both is a category error: volatility disqualifies a name for
    # a vol-sized crossover rule, but is irrelevant (or desirable) for a thesis
    # position you intend to hold through drawdowns.

    @staticmethod
    def core() -> "ScreenCriteria":
        """CORE / buy-and-hold sleeve.

        Buy-and-hold beat the tuned strategy in 27 of 34 walk-forward folds, so
        this pool's edge is selection and patience, not timing. Volatility is
        NOT a filter — MRVL (0.79), MU (0.81) and ALAB (0.98) are legitimate
        holds. Screen only for liquidity, a real share price, and enough trend
        persistence to show the market isn't permanently rejecting the name.
        """
        return ScreenCriteria(min_dollar_volume=20_000_000.0, min_price=5.0,
                              min_vol=0.0, max_vol=99.0,
                              min_trend_persistence=0.25, max_trend_persistence=1.0,
                              label="core")

    @staticmethod
    def options() -> "ScreenCriteria":
        """OPTIONS sleeve — the ~20% pocket.

        Wants the opposite of the tactical screen: enough volatility for
        convexity to be worth paying for, deep liquidity (so the option chain is
        tradeable, not just the stock), and a real share price. Cheap, quiet
        names make lousy long-option candidates — you pay theta and get no move.

        Chain-level checks (IV rank, open interest, bid/ask width, earnings
        dates) need live option data and are NOT done here — see
        app/options/strategies.py. This is the underlying-level pre-filter.
        """
        return ScreenCriteria(min_dollar_volume=100_000_000.0, min_price=10.0,
                              min_vol=0.45, max_vol=99.0,
                              min_trend_persistence=0.20, max_trend_persistence=1.0,
                              label="options")

    @staticmethod
    def tactical() -> "ScreenCriteria":
        """Technical strategies (sma_crossover / rsi_reversion).

        Vol-capped at 0.75 because the sizing scalar clamp(0.30/vol, 0.4, 1.5)
        bottoms out there and can no longer shrink positions proportionally.
        """
        return ScreenCriteria(label="tactical")


@dataclass
class ScreenResult:
    symbol: str
    price: float
    dollar_volume: float
    vol: float
    trend_persistence: float
    ret_3m: float
    passed: bool
    reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"symbol": self.symbol, "price": round(self.price, 2),
                "dollar_volume": round(self.dollar_volume, 0), "vol": round(self.vol, 3),
                "trend_persistence": round(self.trend_persistence, 3),
                "ret_3m_pct": round(self.ret_3m * 100, 1),
                "passed": self.passed, "reasons": self.reasons}


def _metrics(closes: list[float], volumes: list[float]) -> tuple[float, float, float, float]:
    """(annualized vol, avg 20d dollar volume, trend persistence, 3m return)."""
    rets = [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes)) if closes[i - 1]]
    if len(rets) < 20:
        return 0.0, 0.0, 0.0, 0.0
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    vol = math.sqrt(var) * math.sqrt(252)

    tail_c, tail_v = closes[-20:], volumes[-20:]
    dv = sum(c * v for c, v in zip(tail_c, tail_v)) / len(tail_c) if tail_v else 0.0

    above = 0
    counted = 0
    for i in range(50, len(closes)):
        sma = sum(closes[i - 50:i]) / 50
        counted += 1
        if closes[i] > sma:
            above += 1
    persistence = above / counted if counted else 0.0

    lookback = min(63, len(closes) - 1)
    ret3 = closes[-1] / closes[-1 - lookback] - 1.0 if lookback > 0 else 0.0
    return vol, dv, persistence, ret3


def screen(symbols: list[str], criteria: ScreenCriteria | None = None,
           period: str = "1y") -> list[ScreenResult]:
    c = criteria or ScreenCriteria()
    import warnings
    warnings.filterwarnings("ignore")
    import yfinance as yf

    out: list[ScreenResult] = []
    df = yf.download(sorted(set(symbols)), period=period, progress=False,
                     auto_adjust=True, group_by="column")
    if df is None or df.empty:
        return out

    for sym in sorted(set(symbols)):
        try:
            closes = [float(x) for x in df["Close"][sym].dropna().values]
            volumes = [float(x) for x in df["Volume"][sym].dropna().values]
        except Exception:
            out.append(ScreenResult(sym, 0, 0, 0, 0, 0, False, ["no data"]))
            continue
        if len(closes) < 60:
            out.append(ScreenResult(sym, 0, 0, 0, 0, 0, False, ["insufficient history"]))
            continue

        vol, dv, persistence, ret3 = _metrics(closes, volumes)
        price = closes[-1]
        reasons: list[str] = []
        if price < c.min_price:
            reasons.append(f"price ${price:.2f} < ${c.min_price:.0f}")
        if dv < c.min_dollar_volume:
            reasons.append(f"illiquid (${dv / 1e6:.1f}M/day)")
        if vol < c.min_vol:
            reasons.append(f"too quiet (vol {vol:.2f})")
        if vol > c.max_vol:
            reasons.append(f"too volatile (vol {vol:.2f})")
        if persistence < c.min_trend_persistence:
            reasons.append(f"no sustained trend ({persistence:.0%} above 50d)")
        if persistence > c.max_trend_persistence:
            reasons.append(f"never pulls back ({persistence:.0%} above 50d)")

        out.append(ScreenResult(sym, price, dv, vol, persistence, ret3,
                                passed=not reasons, reasons=reasons))
    return out


def candidate_universe() -> list[str]:
    """Holdings + the sector names our sources actually flagged."""
    from ..portfolio.holdings import load_holdings
    held = [str(h["symbol"]).upper() for h in load_holdings()]
    # Named by Serenity (memory / photonics bottlenecks) — see memory/source_scoring.md
    flagged = ["MU", "SNDK", "AXTI", "LITE", "NBIS", "CRWV"]
    return sorted(set(held) | set(flagged))


def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="comma list; default = holdings + flagged names")
    ap.add_argument("--pool", choices=["core", "options", "tactical"], default="tactical",
                     help="which sleeve's screen to apply — they have different jobs")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    syms = ([s.strip().upper() for s in args.symbols.split(",") if s.strip()]
            or candidate_universe())
    crit = {"core": ScreenCriteria.core, "options": ScreenCriteria.options,
            "tactical": ScreenCriteria.tactical}[args.pool]()
    res = screen(syms, crit)
    print(f"[{crit.label} screen] liquidity>=${crit.min_dollar_volume/1e6:.0f}M  "
          f"price>=${crit.min_price:.0f}  vol {crit.min_vol:.2f}-"
          f"{'inf' if crit.max_vol > 10 else f'{crit.max_vol:.2f}'}")

    if args.json:
        print(json.dumps([r.to_dict() for r in res], indent=2))
        return

    passed = [r for r in res if r.passed]
    failed = [r for r in res if not r.passed]
    print(f"screened {len(res)} symbols — {len(passed)} passed\n")
    print(f"{'sym':7}{'price':>9}{'$vol/day':>11}{'vol':>7}{'trend':>8}{'3m':>8}")
    print("-" * 52)
    for r in sorted(passed, key=lambda x: -x.dollar_volume):
        print(f"{r.symbol:7}{r.price:>9.2f}{r.dollar_volume / 1e6:>10.0f}M"
              f"{r.vol:>7.2f}{r.trend_persistence:>8.0%}{r.ret_3m * 100:>7.1f}%")
    print(f"\nrejected ({len(failed)}):")
    for r in sorted(failed, key=lambda x: x.symbol):
        print(f"  {r.symbol:7} {'; '.join(r.reasons)}")


if __name__ == "__main__":
    _main()
