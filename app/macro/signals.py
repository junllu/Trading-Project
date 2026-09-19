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
from datetime import date, datetime, timedelta

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


# --- Bitcoin halving cycle -------------------------------------------------
# Supply issuance halves roughly every 210,000 blocks (~4 years). These four
# dates are protocol events, not estimates; the fifth is a block-height
# projection and moves with hash rate, so it is stored separately.
HALVINGS = ["2012-11-28", "2016-07-09", "2020-05-11", "2024-04-19"]
NEXT_HALVING_EST = "2028-04-20"          # block 1,050,000, projected
HALVING_CYCLE_DAYS = 1458                 # ~4 years, the design target

# THE SAMPLE PROBLEM, STATED UP FRONT.
#
# There are three COMPLETED halving cycles. Three. Every "halving → bull market"
# claim in circulation rests on n=3, and two of those three coincided with the
# largest monetary expansion in modern history — the same M2 impulse this module
# measures separately. The two are not independent, so attributing the move to
# issuance is unfalsifiable with the data that exists.
#
# This project deleted app/macro/timeline.py for exactly this sin: hard-coding
# "ai_boom 2023-2027: semiconductors +0.6", a narrative written after the race,
# whose edge vanished out of sample. So the halving contributes a bias of ZERO.
# It reports WHERE IN THE CYCLE we are, with measured returns and their sample
# size attached, and lets the operator decide. When n reaches something a
# statistician would accept, revisit — not before.
HALVING_CONTRIBUTES_BIAS = False


def _halving_bounds(d: date) -> tuple[date, date, int]:
    """(last halving, next halving, index) around date d."""
    hs = [datetime.strptime(x, "%Y-%m-%d").date() for x in HALVINGS]
    nxt = datetime.strptime(NEXT_HALVING_EST, "%Y-%m-%d").date()
    prior = [h for h in hs if h <= d]
    last = prior[-1] if prior else hs[0]
    following = [h for h in hs + [nxt] if h > d]
    return last, (following[0] if following else nxt), len(prior)


def halving_quartile(d: date | None = None) -> int:
    """1-4: which quarter of the issuance cycle d sits in."""
    d = d or date.today()
    last, _nxt, _i = _halving_bounds(d)
    days = (d - last).days
    return min(4, max(1, days * 4 // HALVING_CYCLE_DAYS + 1))


def halving_cycle(d: date | None = None) -> MacroSignal:
    """Position in the issuance cycle. Deliberately contributes no bias."""
    d = d or date.today()
    last, nxt, idx = _halving_bounds(d)
    since, until = (d - last).days, (nxt - d).days
    q = halving_quartile(d)
    projected = nxt.isoformat() == NEXT_HALVING_EST
    return MacroSignal(
        name="halving_cycle",
        value=0.0 if not HALVING_CONTRIBUTES_BIAS else 0.0,
        detail=(f"halving #{idx} was {last} ({since}d ago), next "
                f"{nxt}{' (projected)' if projected else ''} in {until}d — "
                f"quartile {q}/4. Contributes NO bias: n=3 completed cycles, "
                f"two of them inside the same M2 expansion, so issuance cannot "
                f"be separated from liquidity."),
        as_of=d.isoformat())


def halving_history(symbol: str = "BTC-USD", horizon_days: int = 90) -> dict:
    """Measured forward returns by cycle quartile, with n. Never asserted.

    Reported so the operator can see how thin the evidence is rather than
    being told a conclusion drawn from it.
    """
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        df = yf.download(symbol, start="2014-09-01", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return {"error": "no data"}
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)
        closes = [(i.date(), float(r["Close"])) for i, r in df.iterrows()]
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    by_q: dict[int, list[float]] = {1: [], 2: [], 3: [], 4: []}
    idx = {dt: c for dt, c in closes}
    dates = [dt for dt, _ in closes]
    for i, (dt, c) in enumerate(closes):
        fwd_date = dt + timedelta(days=horizon_days)
        fwd = next((idx[x] for x in dates[i:] if x >= fwd_date), None)
        if fwd is None or c <= 0:
            continue
        by_q[halving_quartile(dt)].append(fwd / c - 1.0)

    out = {}
    for q, vals in by_q.items():
        if not vals:
            out[q] = {"n_days": 0}
            continue
        vals.sort()
        out[q] = {
            "n_days": len(vals),
            "n_independent_cycles": len(HALVINGS) - 1,
            "mean_pct": round(sum(vals) / len(vals) * 100, 1),
            "median_pct": round(vals[len(vals) // 2] * 100, 1),
            "positive_pct": round(sum(1 for v in vals if v > 0) / len(vals) * 100),
        }
    return {
        "symbol": symbol, "horizon_days": horizon_days, "by_quartile": out,
        "caveat": ("Overlapping daily windows inflate n_days enormously: with a "
                   "90-day horizon each cycle contributes ~4 independent "
                   "observations, not ~365. n_independent_cycles is the honest "
                   "count and it is 3."),
    }


# Measured, not folklore. See halving_event_study(): across the three cycles with
# price data the PEAK lands at +525, +546 and +535 days — a 21-day spread. The
# GAIN to that peak decays by roughly 4x each cycle (+2895%, +685%, +95%), which
# is what makes the pattern untradeable even though the timing looks reliable:
# a rule fitted on the first two cycles would have been sized ~7x too large for
# the third. Window kept short so it cannot catch the NEXT cycle's rise, which
# is what made a naive 4-year window report the 2020 peak at +1402d.
MEASURED_PEAK_DAYS = (525, 546, 535)
PEAK_SEARCH_WINDOW_DAYS = 700


def halving_event_study(symbol: str = "BTC-USD") -> dict:
    """Peak timing and magnitude per cycle, plus where today sits.

    Reports each cycle SEPARATELY on purpose. A mean across three events with a
    30x spread in magnitude describes none of them, and averaging is how the
    decay — the only part with an obvious economic story — gets hidden.
    """
    try:
        import warnings
        warnings.filterwarnings("ignore")
        import yfinance as yf
        df = yf.download(symbol, start="2014-09-01", progress=False, auto_adjust=True)
        if df is None or df.empty:
            return {"error": "no data"}
        if hasattr(df.columns, "nlevels") and df.columns.nlevels > 1:
            df.columns = df.columns.get_level_values(0)
        px = {i.date(): float(r["Close"]) for i, r in df.iterrows()}
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    days = sorted(px)
    today = days[-1]
    cycles = []
    for h in HALVINGS:
        h0 = datetime.strptime(h, "%Y-%m-%d").date()
        end = min(h0 + timedelta(days=PEAK_SEARCH_WINDOW_DAYS), today)
        win = [(d, px[d]) for d in days if h0 <= d <= end]
        if len(win) < 200:
            cycles.append({"halving": h, "status": "no price history"})
            continue
        base = win[0][1]
        pk_d, pk_v = max(win, key=lambda x: x[1])
        after = [(d, px[d]) for d in days
                 if pk_d < d <= min(pk_d + timedelta(days=400), today)]
        tr = min(after, key=lambda x: x[1]) if after else None
        cycles.append({
            "halving": h, "status": "measured",
            "peak_date": pk_d.isoformat(), "peak_at_days": (pk_d - h0).days,
            "gain_to_peak_pct": round((pk_v / base - 1) * 100),
            "drawdown_after_peak_pct": round((tr[1] / pk_v - 1) * 100) if tr else None,
            "trough_date": tr[0].isoformat() if tr else None,
        })

    last = datetime.strptime(HALVINGS[-1], "%Y-%m-%d").date()
    cur_win = [(d, px[d]) for d in days if last <= d <= today]
    base = cur_win[0][1]
    pk_d, pk_v = max(cur_win, key=lambda x: x[1])
    now = {
        "days_since_halving": (today - last).days,
        "price": round(px[today], 2),
        "vs_halving_day_pct": round((px[today] / base - 1) * 100),
        "cycle_peak_date": pk_d.isoformat(),
        "cycle_peak_at_days": (pk_d - last).days,
        "from_cycle_peak_pct": round((px[today] / pk_v - 1) * 100),
        "days_since_cycle_peak": (today - pk_d).days,
    }
    return {"symbol": symbol, "cycles": cycles, "now": now,
            "measured_peak_days": list(MEASURED_PEAK_DAYS),
            "caveat": ("n=3. The timing is tight and the magnitude is not: gains to "
                       "peak ran +2895%, +685%, +95%. Any rule sized on the earlier "
                       "cycles is sized wrong for the next one, and three events "
                       "cannot distinguish decay from noise.")}


# Peak -> trough offsets from the two COMPLETED post-peak declines:
#   2017-12-16 -> 2018-12-15 = 364d      2021-11-08 -> 2022-11-21 = 378d
# The 2024 cycle's decline is still running and is therefore not an observation
# yet — including an unfinished drawdown would bias the mean toward whatever
# today happens to be.
MEASURED_TROUGH_OFFSET_DAYS = (364, 378)


def halving_projection(d: date | None = None) -> dict:
    """Where the issuance clock points next, with the sample stated on every line.

    A projection from three events is an ARITHMETIC RESTATEMENT of those events,
    not a forecast, and it is labelled that way. The value is not the date — it
    is seeing how the date sits against the campaign deadline, which is a
    question the cycle folklore never asks.
    """
    d = d or date.today()
    last = datetime.strptime(HALVINGS[-1], "%Y-%m-%d").date()
    nxt = datetime.strptime(NEXT_HALVING_EST, "%Y-%m-%d").date()
    lo, hi = min(MEASURED_PEAK_DAYS), max(MEASURED_PEAK_DAYS)
    mean_off = round(sum(MEASURED_PEAK_DAYS) / len(MEASURED_PEAK_DAYS))
    since = (d - last).days

    # The current cycle's peak is HISTORY, not a projection — every measured
    # offset is already behind us. Presenting it as "upcoming" would be the
    # single most misleading thing this function could do.
    current_peak_passed = since > hi
    cur = {
        "halving": last.isoformat(),
        "days_since": since,
        "peak_window": [(last + timedelta(days=lo)).isoformat(),
                        (last + timedelta(days=hi)).isoformat()],
        "peak_already_passed": current_peak_passed,
    }
    if current_peak_passed:
        pk = last + timedelta(days=mean_off)
        t_lo = pk + timedelta(days=min(MEASURED_TROUGH_OFFSET_DAYS))
        t_hi = pk + timedelta(days=max(MEASURED_TROUGH_OFFSET_DAYS))
        cur["trough_window"] = [t_lo.isoformat(), t_hi.isoformat()]
        cur["trough_window_n"] = len(MEASURED_TROUGH_OFFSET_DAYS)
        cur["in_trough_window"] = t_lo <= d <= t_hi

    nxt_peak = nxt + timedelta(days=mean_off)
    campaign_deadline = date(2027, 12, 31)
    return {
        "as_of": d.isoformat(),
        "peak_offset_days": {"min": lo, "mean": mean_off, "max": hi,
                             "n": len(MEASURED_PEAK_DAYS)},
        "current_cycle": cur,
        "next_cycle": {
            "halving_est": nxt.isoformat(),
            "halving_is_projected": True,
            "days_to_halving": (nxt - d).days,
            "projected_peak": nxt_peak.isoformat(),
            "projected_peak_window": [(nxt + timedelta(days=lo)).isoformat(),
                                      (nxt + timedelta(days=hi)).isoformat()],
            "days_to_projected_peak": (nxt_peak - d).days,
        },
        "vs_campaign": {
            "deadline": campaign_deadline.isoformat(),
            "next_peak_after_deadline_days": (nxt_peak - campaign_deadline).days,
            "verdict": ("the next issuance-cycle peak lands AFTER the campaign "
                        "deadline — this clock offers the campaign nothing"
                        if nxt_peak > campaign_deadline else
                        "the next peak falls inside the campaign window"),
        },
        "caveat": ("Projection = mean of three measured offsets applied to a "
                   "block-height estimate. n=3 on timing, n=2 on the trough. The "
                   "gain to each peak decayed ~4x per cycle (+2895%, +685%, +95%), "
                   "so the DATE is the only part with any consistency — never the "
                   "magnitude."),
    }


def all_signals(d: date | None = None, with_m2: bool = True) -> list[MacroSignal]:
    out = [presidential_cycle(d)]
    if with_m2:
        out.append(m2_liquidity(d))
    out.append(halving_cycle(d))
    return out


# Signals that report position but deliberately contribute no bias. They must be
# excluded from the average, not averaged in as zeros: a 0.0 term is not neutral,
# it drags the blend toward zero and silently weakens every real signal in it.
# Adding the halving cycle as a zero would have cut the existing macro tilt by a
# third while looking like an addition.
NON_CONTRIBUTING = {"halving_cycle"}


def blended_bias(d: date | None = None, with_m2: bool = True) -> float:
    sigs = [s for s in all_signals(d, with_m2) if s.name not in NON_CONTRIBUTING]
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
