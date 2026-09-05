"""Black-Scholes option pricing and Greeks — pure Python (math only).

Used to estimate premiums, deltas, and assignment probabilities when a live
options chain isn't wired in. When you connect a broker options feed, prefer
the real bid/ask; use this for theoretical values and what-if analysis.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


@dataclass
class Greeks:
    price: float
    delta: float
    gamma: float
    theta: float          # per day
    vega: float           # per 1 vol point (0.01)
    prob_itm: float       # risk-neutral probability of finishing in the money


def black_scholes(spot: float, strike: float, days: float, vol: float,
                  rate: float = 0.045, is_call: bool = True) -> Greeks:
    """Price a European option and its Greeks.

    spot/strike in $, days to expiry, vol as annualized decimal (0.30 = 30%).
    """
    t = max(days, 0.0) / 365.0
    if t <= 0 or vol <= 0 or spot <= 0:
        intrinsic = max(0.0, (spot - strike) if is_call else (strike - spot))
        return Greeks(intrinsic, 1.0 if intrinsic > 0 else 0.0, 0.0, 0.0, 0.0,
                      1.0 if intrinsic > 0 else 0.0)

    d1 = (math.log(spot / strike) + (rate + 0.5 * vol * vol) * t) / (vol * math.sqrt(t))
    d2 = d1 - vol * math.sqrt(t)
    disc = math.exp(-rate * t)

    if is_call:
        price = spot * _norm_cdf(d1) - strike * disc * _norm_cdf(d2)
        delta = _norm_cdf(d1)
        prob_itm = _norm_cdf(d2)
    else:
        price = strike * disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)
        delta = _norm_cdf(d1) - 1.0
        prob_itm = _norm_cdf(-d2)

    gamma = _norm_pdf(d1) / (spot * vol * math.sqrt(t))
    vega = spot * _norm_pdf(d1) * math.sqrt(t) * 0.01
    theta_annual = (-(spot * _norm_pdf(d1) * vol) / (2 * math.sqrt(t))
                    - (1 if is_call else -1) * rate * strike * disc
                    * _norm_cdf(d2 if is_call else -d2))
    theta = theta_annual / 365.0

    return Greeks(price=round(price, 4), delta=round(delta, 4), gamma=round(gamma, 6),
                  theta=round(theta, 4), vega=round(vega, 4), prob_itm=round(prob_itm, 4))


def implied_vol_guess(symbol: str) -> float:
    """A rough default annualized vol when we have no live IV.

    Bucketed by asset type so estimates aren't wildly off. Replace with real IV
    from an options chain as soon as one is connected.
    """
    high_vol = {"NVDA", "MRVL", "ALAB", "BE", "RDDT", "BULL", "CCXI", "AEIS", "VRT", "SMH"}
    etf_low = {"SPY", "QQQ", "JEPQ", "TLT", "XLE", "XLF", "GLD"}
    s = symbol.upper()
    if s in etf_low:
        return 0.18
    if s in high_vol:
        return 0.55
    return 0.35
