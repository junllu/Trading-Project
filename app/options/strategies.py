"""Income option strategies: covered calls, cash-secured puts, sell-the-news.

These generate *plans for your review* — sized to real holdings and cash, with
estimated premiums and assignment odds from Black-Scholes. They do not place
option orders automatically (options assignment risk warrants a human tap), and
every plan carries explicit warnings.

Premiums are theoretical (BS) until a live options chain is connected; treat
them as ballpark, not fills.
"""
from __future__ import annotations

from ..models import Position
from .models import OptionContract, OptionPlan, OptionQuote, OptionType
from .pricing import black_scholes, implied_vol_guess


def _quote(symbol: str, spot: float, strike: float, days: int, is_call: bool) -> OptionQuote:
    iv = implied_vol_guess(symbol)
    g = black_scholes(spot, strike, days, iv, is_call=is_call)
    return OptionQuote(
        contract=OptionContract(symbol, OptionType.CALL if is_call else OptionType.PUT, strike, days),
        premium=g.price, delta=g.delta, prob_itm=g.prob_itm, theta=g.theta, iv=iv,
    )


def _annualized(premium_total: float, capital: float, days: int) -> float:
    if capital <= 0 or days <= 0:
        return 0.0
    return (premium_total / capital) * (365.0 / days)


def covered_call_candidates(positions: list[Position], prices: dict[str, float],
                            days: int = 30, otm_pct: float = 0.05) -> list[OptionPlan]:
    """For each 100+ share holding, suggest selling an OTM call against it.

    Generates income; caps upside at the strike. One contract per 100 shares.
    """
    plans: list[OptionPlan] = []
    for pos in positions:
        lots = int(pos.quantity // 100)
        if lots < 1:
            continue
        spot = prices.get(pos.symbol, pos.avg_price)
        if spot <= 0:
            continue
        strike = round(spot * (1 + otm_pct), 2)
        q = _quote(pos.symbol, spot, strike, days, is_call=True)
        premium_total = q.premium * 100 * lots
        capital = spot * 100 * lots           # shares already owned back the calls
        plan = OptionPlan(
            strategy="covered_call", symbol=pos.symbol,
            action=f"Sell {lots} {pos.symbol} {strike:g}C ~{days}d (covered by {lots*100} shares)",
            contracts=lots, quote=q, est_premium=premium_total,
            annualized_return=_annualized(premium_total, capital, days),
            rationale=(f"Own {pos.quantity:g} sh @ {pos.avg_price:g}; sell {otm_pct*100:.0f}% OTM call "
                       f"for ${premium_total:,.0f} credit. Assignment odds ~{q.prob_itm*100:.0f}%."),
        )
        if strike < pos.avg_price:
            plan.warnings.append(f"strike {strike:g} is below your cost {pos.avg_price:g} — assignment would lock a loss")
        if q.prob_itm > 0.4:
            plan.warnings.append("elevated assignment probability — consider a higher strike")
        plans.append(plan)
    return sorted(plans, key=lambda p: -p.annualized_return)


def cash_secured_put_candidates(symbols: list[str], prices: dict[str, float], cash: float,
                                days: int = 30, otm_pct: float = 0.05) -> list[OptionPlan]:
    """Suggest selling OTM puts to get paid while waiting to buy lower.

    Only proposes contracts fully covered by available cash (strike x100).
    """
    plans: list[OptionPlan] = []
    remaining = cash
    for sym in symbols:
        spot = prices.get(sym, 0.0)
        if spot <= 0:
            continue
        strike = round(spot * (1 - otm_pct), 2)
        collateral = strike * 100
        contracts = int(remaining // collateral)
        if contracts < 1:
            continue
        contracts = min(contracts, 3)         # keep suggestions sane
        q = _quote(sym, spot, strike, days, is_call=False)
        premium_total = q.premium * 100 * contracts
        capital = collateral * contracts
        plans.append(OptionPlan(
            strategy="cash_secured_put", symbol=sym,
            action=f"Sell {contracts} {sym} {strike:g}P ~{days}d (secure ${capital:,.0f} cash)",
            contracts=contracts, quote=q, est_premium=premium_total,
            annualized_return=_annualized(premium_total, capital, days),
            rationale=(f"Get paid ${premium_total:,.0f} to agree to buy {sym} at {strike:g} "
                       f"({otm_pct*100:.0f}% below {spot:g}). Assignment odds ~{q.prob_itm*100:.0f}%."),
            warnings=(["assignment means buying 100 sh/contract — only sell puts on names you want to own"]),
        ))
    return sorted(plans, key=lambda p: -p.annualized_return)


def sell_the_news_plan(symbol: str, spot: float, sentiment_score: float, holds_shares: float,
                       importance: float = 0.7, days: int = 21) -> OptionPlan | None:
    """'Sell the news': after a strong-sentiment event, monetize the move.

    - If you HOLD the name and sentiment spiked positive → sell a call into
      strength (take premium / trim exposure at a higher strike).
    - If sentiment turned sharply negative → suggest a protective/defined action.
    """
    if abs(sentiment_score) < 0.3 or importance < 0.4:
        return None

    if sentiment_score > 0 and holds_shares >= 100:
        lots = int(holds_shares // 100)
        strike = round(spot * 1.07, 2)
        q = _quote(symbol, spot, strike, days, is_call=True)
        premium_total = q.premium * 100 * lots
        return OptionPlan(
            strategy="sell_the_news", symbol=symbol,
            action=f"Sell {lots} {symbol} {strike:g}C ~{days}d into positive-news strength",
            contracts=lots, quote=q, est_premium=premium_total,
            annualized_return=_annualized(premium_total, spot * 100 * lots, days),
            rationale=(f"Bullish event (score {sentiment_score:+.2f}) — 'sell the news' by writing calls "
                       f"into the spike for ${premium_total:,.0f}, monetizing elevated IV."),
            warnings=["caps upside if the rally continues past the strike"],
        )
    if sentiment_score < 0:
        return OptionPlan(
            strategy="sell_the_news", symbol=symbol,
            action=f"Review downside protection on {symbol} (buy put / trim) — bearish news",
            contracts=0, est_premium=0.0,
            rationale=(f"Bearish event (score {sentiment_score:+.2f}). 'Sell the news' on the long side: "
                       f"consider trimming or a protective put; avoid selling puts into falling knives."),
            warnings=["directional risk — size any hedge to conviction"],
        )
    return None
