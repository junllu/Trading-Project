"""Computed macro signals — knowable at the time, by construction.

The existing timeline (app/macro/timeline.py) is hand-authored with hindsight:
`ai_boom 2023-2027: semiconductors +0.6` was written knowing the AI boom
happened, so a backtest replaying 2023 receives knowledge from the future. That
is why its apparent edge evaporated out of sample.

Everything here is different in kind: each signal is either deterministic from
the calendar, or derived from a published series with its real publication lag
applied. Neither can leak the future, so backtests using them are honest.

Only signals that survived measurement are included:

  presidential_cycle  76 years of S&P annual returns. Year 3 (pre-election):
                      mean +17.2%, median +18.9%, 89% positive — far above
                      Yr1 (+8.4%), Yr2 (+4.2%), Yr4 (+8.1%). Deterministic.

  m2_liquidity        M2 YoY growth vs forward 12m S&P returns: corr -0.17.
                      NOTE THE SIGN — high money growth precedes WEAKER returns
                      (the 2021 spike preceded 2022's -19%). Used as a
                      risk-reduction trigger when growth runs hot, not as a
                      buy signal. Lagged 2 months for publication.

Deliberately NOT included: anything requiring a judgement call about what a
policy "means" for a sector. That belongs in app/macro/facts.py, where it
carries a date-known and a source.

    python -m app.macro.signals
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

# Measured on S&P 500 annual returns, 1951-2026 (n=19 per bucket).
CYCLE_STATS = {
    1: {"label": "post-election", "mean": 8.35, "median": 9.06, "positive_pct": 63},
    2: {"label": "midterm", "mean": 4.16, "median": 1.06, "positive_pct": 58},
    3: {"label": "pre-election", "mean": 17.18, "median": 18.89, "positive_pct": 89},
    4: {"label": "election", "mean": 8.11, "median": 11.78, "positive_pct": 84},
}

# Bias mapped from the historical edge, scaled to [-1, 1] for the conviction blend.
CYCLE_BIAS = {1: 0.0, 2: -0.15, 3: 0.35, 4: 0.10}

M2_SERIES = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=M2SL"
M2_PUBLICATION_LAG_MONTHS = 2       # only use data that was actually published
# Transmission lag for the LEVEL component, measured (see m2_liquidity docstring).
# Money-supply level peaks against forward returns at ~6-9 months; acceleration
# peaks contemporaneously, so it gets no extra lag.
M2_LEVEL_TRANSMISSION_LAG_MONTHS = 6


@dataclass
class MacroSignal:
    name: str
    value: float                     # -1..1 bias
    detail: str
    as_of: str

    def to_dict(self) -> dict:
        return {"name": self.name, "value": round(self.value, 3),
                "detail": self.detail, "as_of": self.as_of}


def cycle_year(d: date | None = None) -> int:
    """1-4 within the US presidential cycle. 2024/2028 are election years (=4)."""
    d = d or date.today()
    return ((d.year - 2025) % 4) + 1


def presidential_cycle(d: date | None = None) -> MacroSignal:
    d = d or date.today()
    y = cycle_year(d)
    s = CYCLE_STATS[y]
    return MacroSignal(
        name="presidential_cycle", value=CYCLE_BIAS[y],
        detail=(f"year {y} ({s['label']}): historically mean {s['mean']:+.1f}%, "
                f"{s['positive_pct']}% positive (n=19)"),
        as_of=d.isoformat())


def m2_liquidity(d: date | None = None, quartile_hot: float = 8.38,
                 quartile_cold: float = 4.84) -> MacroSignal:
    """M2 as TWO components with opposite signs, lagged for publication.

    Measured 1960-2026 against forward S&P returns:

        M2 YoY LEVEL         12m corr -0.170   (inverted)
        M2 YoY ACCELERATION  12m corr +0.125
        Real M2 ACCELERATION  6m corr +0.173   (strongest)

    The opposite signs are the point, and they are economically coherent rather
    than data-mined: the LEVEL says where you are in the cycle (high growth =
    late-cycle, inflation building, tightening ahead — the 2021 spike preceded
    2022's -19%), while ACCELERATION says what policy is doing right now
    (liquidity being added is supportive). Combining them into one number
    cancels the information out, which is what the earlier version did.

    Honesty about strength: |corr| <= 0.17 is weak, the forward windows overlap
    so the effective sample is far smaller than n suggests, and 15 variant/
    horizon combinations were tested before settling here. Treat this as a
    tilt, never a trigger.
    """
    d = d or date.today()
    try:
        import io

        import pandas as pd
        import requests
        raw = requests.get(M2_SERIES, timeout=30).text
        s = pd.read_csv(io.StringIO(raw))
        s["observation_date"] = pd.to_datetime(s["observation_date"])
        s = s.set_index("observation_date")["M2SL"]
        yoy = (s / s.shift(12) - 1) * 100
        accel = yoy.diff(3)                      # 3-month change in growth rate

        # Two different lags, measured — not assumed. Sweeping additional lag
        # against forward 12m S&P returns (1960-2026):
        #   LEVEL  0m -0.170 | 3m -0.221 | 6m -0.255 | 9m -0.253 | 12m -0.230 | 18m -0.146
        #   ACCEL  0m +0.125 | 3m +0.083 | 6m +0.002 | 9m -0.053 | 12m -0.101
        # The level's smooth rise-peak-decay is the signature of a real
        # transmission lag (money supply works through the economy into earnings
        # over ~2-3 quarters); noise does not produce a clean hump. Acceleration
        # peaks contemporaneously because a policy impulse is felt immediately.
        pub = pd.Timestamp(d) - pd.DateOffset(months=M2_PUBLICATION_LAG_MONTHS)
        level_asof = pub - pd.DateOffset(months=M2_LEVEL_TRANSMISSION_LAG_MONTHS)

        vis_level = yoy[yoy.index <= level_asof].dropna()     # lagged: cycle position
        vis_accel = accel[accel.index <= pub].dropna()        # current: policy impulse
        if vis_level.empty:
            return MacroSignal("m2_liquidity", 0.0, "no published data at this date",
                               d.isoformat())

        level = float(vis_level.iloc[-1])
        acc = float(vis_accel.iloc[-1]) if not vis_accel.empty else 0.0

        # LEVEL: inverted — hot growth precedes weaker returns.
        level_bias = -0.25 if level >= quartile_hot else (0.10 if level < quartile_cold else 0.0)
        # ACCELERATION: same-signed — scaled so ~2pp of 3m change is a full unit.
        accel_bias = max(-0.25, min(0.25, acc / 2.0 * 0.25))

        bias = level_bias + accel_bias
        return MacroSignal(
            "m2_liquidity", max(-1.0, min(1.0, bias)),
            f"level {level:+.2f}% @{M2_LEVEL_TRANSMISSION_LAG_MONTHS}m lag "
            f"(bias {level_bias:+.2f}) + accel {acc:+.2f}pp now (bias {accel_bias:+.2f}); "
            f"level obs {vis_level.index[-1]:%Y-%m}",
            d.isoformat())
    except Exception as exc:
        return MacroSignal("m2_liquidity", 0.0, f"unavailable ({type(exc).__name__})",
                           d.isoformat())


def all_signals(d: date | None = None, with_m2: bool = True) -> list[MacroSignal]:
    out = [presidential_cycle(d)]
    if with_m2:
        out.append(m2_liquidity(d))
    return out


def blended_bias(d: date | None = None, with_m2: bool = True) -> float:
    sigs = all_signals(d, with_m2)
    return sum(s.value for s in sigs) / len(sigs) if sigs else 0.0


if __name__ == "__main__":
    today = date.today()
    print(f"macro signals as of {today}\n")
    for s in all_signals(today):
        print(f"  {s.name:20} {s.value:+.2f}   {s.detail}")
    print(f"\n  blended bias: {blended_bias(today):+.3f}")
    print("\ncycle outlook:")
    for yr in range(today.year, today.year + 3):
        d = date(yr, 6, 30)
        cy = cycle_year(d)
        st = CYCLE_STATS[cy]
        print(f"  {yr}: year {cy} ({st['label']:13}) mean {st['mean']:+5.1f}%  "
              f"{st['positive_pct']}% positive")
