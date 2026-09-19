"""Trade the volatility around a held thesis, without ever leaving it.

WHAT THIS IS FOR

The stated goal is a portfolio built on a macro thesis, with the volatility
around each position traded to improve cost basis and take some profit off the
table. Neither existing sleeve does that:

    sma_crossover   trend-following. Sells weakness and buys strength — the
                    opposite trade — and opens and closes WHOLE positions, so
                    it exits the thesis entirely. Observed buying and selling
                    SHEL inside four minutes.
    rsi_reversion   right direction, but it also trades the full position and
                    its trigger is an indicator level rather than anything
                    about the position's own cost.

This is a band, or inventory, strategy. It holds a CORE that is never sold, and
trades a sleeve above it: trim into strength, add back on reversion. The thesis
survives every round trip by construction, which is the whole point — a thesis
you can be stopped out of is not a thesis.

THE BAND IS THE NAME'S OWN, NOT A CONSTANT

Bands are set from each symbol's measured adverse excursion (`mae_study`), so a
name that routinely travels 15% gets a wider band than one that travels 4%. A
fixed percentage band would trade CVX constantly and never touch BE — the same
failure the MAE work already found for stops.

WHAT IT REFUSES TO DO

  * Never sells below `core_fraction` of the position. Harvesting a thesis to
    zero is the failure mode of every scale-out rule that forgets why it holds.
  * Never adds above the original size. This lowers cost basis; it does not
    build a bigger bet while claiming to manage one.
  * Trades only what it can measure. A symbol with no excursion history is
    skipped rather than given a guessed band.
"""
from __future__ import annotations

from typing import Any

from ..models import Side, Signal
from .base import Strategy, StrategyContext

# Fractions of the name's own typical adverse move. A band at 1.0x its median
# excursion is inside ordinary noise and would trade constantly; wider than 2x
# and it rarely triggers. Fixed here BEFORE any outcome is examined — a band
# tuned until the backtest looked good is a fitted parameter.
DEFAULT_TRIM_BAND = 1.5      # x median MAE above cost -> trim
DEFAULT_ADD_BAND = 1.0       # x median MAE below cost -> add back
DEFAULT_CORE_FRACTION = 0.6  # never sell below this share of the position
DEFAULT_SLICE = 0.15         # fraction of the position traded per signal

# A band narrower than this is not a band, it is noise with a number on it.
# Measured on the live book, recent listings produced absurd values — BULL
# 0.6%, CCXI 0.8%, SOC 1.3% — because a short history has not yet contained a
# real drawdown. Trading a 0.9% trim band would churn the position daily and
# pay the spread every time, which is the exact failure this sleeve exists to
# avoid. Names below the floor are skipped, not clamped: a floor applied to an
# unreliable measurement produces a confident number from bad data.
MIN_BAND_PCT = 3.0
# And a distribution needs enough entries to have seen a bad stretch at all.
MIN_ENTRIES = 250


class VolatilityBand(Strategy):
    """Trim into strength, add on reversion, never leave the core."""

    name = "volatility_band"

    def _band_pct(self, symbol: str) -> float | None:
        """The name's own typical adverse move, or None if it cannot be trusted.

        Returns None rather than a fallback in three cases — too few entries,
        an implausibly narrow band, or no study at all. All three mean the same
        thing: this name's normal range has not been established, and trading a
        band derived from it would be trading a number rather than a measurement.
        """
        try:
            from ..analytics.mae_study import study_symbol
            s = study_symbol(symbol)
        except Exception:
            return None
        if not s.get("available") or not s.get("median_mae_pct"):
            return None
        if int(s.get("entries") or 0) < MIN_ENTRIES:
            return None
        band = abs(float(s["median_mae_pct"]))
        return band if band >= MIN_BAND_PCT else None

    def evaluate(self, ctx: StrategyContext) -> list[Signal]:
        if ctx.position_qty <= 0:
            return []                       # this sleeve manages a holding; it opens none

        cost = float(ctx.params.get("avg_price") or 0.0)
        px = ctx.history[-1] if ctx.history else 0.0
        if cost <= 0 or px <= 0:
            return []

        band = self._band_pct(ctx.symbol)
        if band is None:
            return []                       # unmeasured name: skipped, never guessed

        trim_at = float(self.params.get("trim_band", DEFAULT_TRIM_BAND)) * band
        add_at = float(self.params.get("add_band", DEFAULT_ADD_BAND)) * band
        core_frac = float(self.params.get("core_fraction", DEFAULT_CORE_FRACTION))
        slice_frac = float(self.params.get("slice", DEFAULT_SLICE))

        move_pct = (px / cost - 1.0) * 100.0
        original = float(ctx.params.get("original_qty") or ctx.position_qty)
        core_qty = original * core_frac

        # --- trim into strength ------------------------------------------
        if move_pct >= trim_at:
            sellable = ctx.position_qty - core_qty
            if sellable <= 0:
                return []                   # already down to the core; thesis holds
            qty = min(sellable, original * slice_frac)
            if qty <= 0:
                return []
            return [Signal(ctx.symbol, Side.SELL, order_value=round(qty * px, 2),
                           strength=min(1.0, move_pct / max(trim_at, 1e-9)),
                           strategy=self.name,
                           note=(f"+{move_pct:.1f}% vs cost, past {trim_at:.1f}% band "
                                 f"({band:.1f}% median MAE x{trim_at / band:.1f}) — "
                                 f"trim {slice_frac:.0%}, core held"))]

        # --- add back on reversion ---------------------------------------
        if move_pct <= -add_at and ctx.position_qty < original:
            room = original - ctx.position_qty
            qty = min(room, original * slice_frac)
            if qty <= 0:
                return []
            return [Signal(ctx.symbol, Side.BUY, order_value=round(qty * px, 2),
                           strength=min(1.0, abs(move_pct) / max(add_at, 1e-9)),
                           strategy=self.name,
                           note=(f"{move_pct:.1f}% vs cost, past -{add_at:.1f}% band "
                                 f"— add {slice_frac:.0%} back, lowering basis"))]
        return []
