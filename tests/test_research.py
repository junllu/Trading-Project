"""Tests for the research layer and the autonomy/risk gates.

These modules decide what we hold and what the system is allowed to do, and
until now they were the only ones in the repo with no coverage. The tests below
target the JUDGEMENTS rather than the plumbing — that a price spike is told
apart from volume growth, that an unknown action fails closed, that a cap
scales with the account it is applied to. A smoke test that only proves the
import works would not have caught any of the bugs found while writing these.
"""
from __future__ import annotations

import pytest

from app.agent import autonomy, chief, roster
from app.analytics import calibration, cycle_sectors, discovery, peer_value
from app.backtest import costs, trials
from app.config import RiskLimits
from app.ml import meta_label
from app.engine.risk import RiskManager
from app.intel import filings, llm_extract, onboard, operator_edge
from app.intel.thesis import BROKEN, INTACT, UNGRADED, WATCH, Checkpoint, metrics
from app.models import Account, Order, Position, Side
from app.options import desk


# --- filings: price vs volume ---------------------------------------------

def _entry(rev_now, rev_prior, cogs_now, cogs_prior, inv_now=None, inv_prior=None, days=91):
    return {"revenue_current": rev_now, "revenue_prior": rev_prior,
            "cogs_current": cogs_now, "cogs_prior": cogs_prior,
            "inventory_current": inv_now, "inventory_prior": inv_prior,
            "days_in_period": days, "basis": "quarterly", "filing": "test"}


def test_price_spike_detected_when_costs_do_not_follow_revenue():
    """MU's real shape: revenue quadruples, cost barely moves."""
    op = filings.analyse("TEST", _entry(41456, 9301, 6400, 5793, 8567, 8355))
    assert op.character == "PRICE_SPIKE"
    assert op.cost_elasticity == pytest.approx(0.03, abs=0.01)
    # ~97% of the growth is price, and that must be stated, not implied.
    assert op.price_share_pct > 90
    assert any("PRICE" in f for f in op.flags)


def test_margin_pressure_when_costs_outrun_revenue():
    """ANET's real shape: growing, but buying it with price."""
    op = filings.analyse("TEST", _entry(3035.7, 2204.8, 1125.4, 766.2, 2535.3, 2247.1))
    assert op.character == "MARGIN_PRESSURE"
    assert op.cost_elasticity > 1.0
    assert op.margin_delta_pp < 0
    assert any("gross margin down" in f for f in op.flags)


def test_volume_with_operating_leverage_is_the_healthy_shape():
    """VRT's real shape: costs scale sub-linearly and margin expands."""
    op = filings.analyse("TEST", _entry(3274.3, 2638.1, 2039.4, 1741.5))
    assert op.character == "VOLUME+LEVERAGE"
    assert op.margin_delta_pp > 0


def test_falling_and_rising_inventory_days_read_oppositely_in_a_spike():
    """The distinction that separates a real shortage from a stock build."""
    falling = filings.analyse("A", _entry(41456, 9301, 6400, 5793, 8567, 8355))
    rising = filings.analyse("B", _entry(20248, 7355, 5776, 5143, 2698, 2079, days=365))
    assert any("FALLING" in f for f in falling.flags)
    assert any("RISING" in f for f in rising.flags)


def test_no_elasticity_claimed_when_revenue_did_not_grow():
    """Dividing by ~zero growth would manufacture a character. It must not."""
    op = filings.analyse("TEST", _entry(1000, 1000, 500, 400))
    assert op.cost_elasticity is None
    assert op.character == "FLAT"


# --- thesis: checkpoints and the cyclical trap ----------------------------

def test_checkpoint_grades_pass_warn_and_break():
    cp = Checkpoint("m", ">=", 10.0, why="x", warn_at=8.0)
    assert cp.grade(12.0)[0] == INTACT
    assert cp.grade(9.0)[0] == WATCH      # inside the warning band
    assert cp.grade(5.0)[0] == BROKEN
    assert cp.grade(None)[0] == UNGRADED  # missing data is not a pass


def test_checkpoint_respects_direction_of_the_test():
    cp = Checkpoint("m", "<=", 10.0, why="x")
    assert cp.grade(5.0)[0] == INTACT
    assert cp.grade(15.0)[0] == BROKEN


def test_peak_margin_watch_fires_on_record_margin_with_a_low_multiple():
    """The cyclical trap: the low P/E is a SYMPTOM of peak earnings."""
    m = metrics({"pe_ratio": 23.0, "sector": "memory", "quarters": [
        {"end": "2026-05-28", "revenue_m": 41456, "net_margin": 68.13},
        {"end": "2026-02-26", "revenue_m": 23860, "net_margin": 57.77},
        {"end": "2025-11-27", "revenue_m": 13643, "net_margin": 38.41},
        {"end": "2025-08-28", "revenue_m": 11315, "net_margin": 28.29},
        {"end": "2025-05-29", "revenue_m": 9301, "net_margin": 20.27},
    ]})
    assert m["quarters_since_margin_peak"] == 0
    assert m["peak_margin_watch"] is True


def test_peak_margin_watch_does_not_fire_on_a_non_cyclical_name():
    m = metrics({"pe_ratio": 23.0, "sector": "software", "quarters": [
        {"end": "2026-06-30", "revenue_m": 100, "net_margin": 40.0},
        {"end": "2026-03-31", "revenue_m": 90, "net_margin": 38.0},
    ]})
    assert m["peak_margin_watch"] is False


def test_flagged_one_time_margins_are_excluded_from_the_trailing_average():
    """MRVL's 91.65% quarter is an accounting event, not the business."""
    e = {"pe_ratio": 70.0, "sector": "semiconductors",
         "margin_outliers": ["2025-11-01 is a one-time item"],
         "quarters": [
             {"end": "2026-08-01", "revenue_m": 2739.3, "net_margin": 11.24},
             {"end": "2026-05-02", "revenue_m": 2417.8, "net_margin": 1.43},
             {"end": "2026-01-31", "revenue_m": 2218.7, "net_margin": 17.85},
             {"end": "2025-11-01", "revenue_m": 2074.5, "net_margin": 91.65},
             {"end": "2025-08-02", "revenue_m": 2006.1, "net_margin": 9.71},
         ]}
    assert metrics(e)["net_margin_ttm"] < 20      # 91.65 excluded
    assert metrics(e)["margin_peak_pct"] != 91.65


# --- options desk: the structural refusals --------------------------------

def test_desk_sells_premium_when_vol_is_rich_against_its_own_history():
    verdict, _, reasons = desk.structure_for(vol=0.98, rank=91, days=23, held_lots=0)
    assert verdict == desk.CREDIT_SPREAD
    assert any("expensive" in r for r in reasons)
    # Just below the threshold must NOT sell premium — the boundary is the rule.
    assert desk.structure_for(vol=0.98, rank=desk.RICH_RANK - 1, days=23)[0] == desk.NO_TRADE


def test_desk_buys_convexity_only_when_vol_is_cheap_AND_the_name_moves():
    cheap_and_lively = desk.structure_for(vol=0.70, rank=12, days=20, held_lots=0)
    cheap_but_inert = desk.structure_for(vol=0.20, rank=12, days=20, held_lots=0)
    assert cheap_and_lively[0] == desk.DEFINED_RISK_DIRECTIONAL
    # Cheap options on something that does not move are still worthless.
    assert cheap_but_inert[0] == desk.NO_TRADE


def test_desk_returns_no_trade_without_a_catalyst():
    """A thesis is not a catalyst; theta is certain either way."""
    verdict, _, reasons = desk.structure_for(vol=0.98, rank=91, days=None)
    assert verdict == desk.NO_TRADE
    assert any("catalyst" in r for r in reasons)


def test_desk_takes_no_view_when_vol_is_mid_range():
    verdict, _, reasons = desk.structure_for(vol=0.60, rank=50, days=20)
    assert verdict == desk.NO_TRADE
    assert any("mid-range" in r for r in reasons)


def test_desk_will_not_rank_a_level_it_has_no_distribution_for():
    """A vol level without its own history is not a signal."""
    verdict, _, _ = desk.structure_for(vol=0.90, rank=None, days=20)
    assert verdict == desk.NO_TRADE


def test_desk_is_independent_of_the_core_leg_for_uncoordinated_structures():
    """The regression guard for the coupling bug.

    ADBE was previously refused at vol 0.51 with earnings in 3 days purely
    because the CORE layer had no filing snapshot for it. The options leg must
    reach a verdict with no fundamental input whatsoever.
    """
    verdict, _, _ = desk.structure_for(vol=0.51, rank=91, days=3, held_lots=0)
    assert verdict == desk.CREDIT_SPREAD
    assert verdict not in desk.COORDINATED_STRUCTURES


# --- the ONLY coupling: covered calls and cash-secured puts ---------------

def test_holding_the_shares_turns_a_premium_sale_into_a_coordinated_structure():
    verdict, _, _ = desk.structure_for(vol=0.98, rank=91, days=23, held_lots=2)
    assert verdict == desk.COVERED_CALL
    assert verdict in desk.COORDINATED_STRUCTURES


def test_core_pool_vetoes_a_covered_call_on_a_live_thesis():
    for cv in ("INTACT", "WATCH"):
        v, coord, reasons = desk.coordinate("MRVL", desk.COVERED_CALL, cv, [])
        assert v == desk.BLOCKED
        assert "vetoed" in coord
        assert any("sold the winner early" in r for r in reasons)


def test_covered_call_clears_when_no_live_thesis_protects_the_shares():
    v, coord, _ = desk.coordinate("WEN", desk.COVERED_CALL, "BROKEN", [])
    assert v == desk.COVERED_CALL
    assert "cleared" in coord


def test_cash_secured_put_requires_a_name_the_core_pool_would_own():
    ok, _, _ = desk.coordinate("MU", desk.CASH_SECURED_PUT, "INTACT", [])
    no, _, _ = desk.coordinate("MU", desk.CASH_SECURED_PUT, "WATCH", [])
    assert ok == desk.CASH_SECURED_PUT
    assert no == desk.BLOCKED


def test_coordination_never_touches_an_independent_structure():
    """A core veto must not reach a structure that risks only sleeve premium."""
    for structure in (desk.CREDIT_SPREAD, desk.DEFINED_RISK_DIRECTIONAL):
        v, coord, _ = desk.coordinate("ADBE", structure, "INTACT", [])
        assert v == structure
        assert "independent" in coord


# --- autonomy: the approval line and receipts -----------------------------

def test_reversible_actions_finish_and_irreversible_ones_park():
    assert autonomy.classify_action("research")[0] == "FINISH"
    assert autonomy.classify_action("stage")[0] == "FINISH"
    assert autonomy.classify_action("place_order")[0] == "PARK"
    assert autonomy.classify_action("delete")[0] == "PARK"


def test_unknown_actions_fail_closed():
    """The whole point of the rule: an unclassified action is not safe."""
    verdict, reason = autonomy.classify_action("liquidate_everything")
    assert verdict == "PARK"
    assert "unclassified" in reason


def test_a_hand_run_cycle_never_counts_as_the_schedule_firing():
    assert "manual" not in autonomy.LIVE_SESSIONS
    assert "closed" not in autonomy.LIVE_SESSIONS
    assert "regular" in autonomy.LIVE_SESSIONS


def test_receipt_recommends_demotion_when_the_pass_rate_decays():
    r = autonomy.Receipt(routine="x", runs=10, passed=5)
    assert r.pass_rate == 0.5
    assert "DEMOTE" in r.recommendation()


def test_receipt_will_not_promote_on_a_repeated_failure_even_at_full_pass_rate():
    """A perfect pass rate must not launder a known recurring fault."""
    clean = autonomy.Receipt(routine="x", runs=10, passed=10)
    broken = autonomy.Receipt(routine="x", runs=10, passed=10,
                              repeated_failures=["source login expired"])
    assert clean.recommendation() == "eligible for promotion"
    assert broken.recommendation() == "repeated failure — repair before any promotion"


def test_receipt_cannot_judge_a_routine_that_never_ran():
    assert "cannot" in autonomy.Receipt(routine="x").recommendation()


# --- live loop: the circuit breaker ---------------------------------------

class _ExplodingPortal:
    """A portal whose every cycle fails, to prove the loop gives up."""
    def __init__(self):
        self.calls = 0

    def ensure_built(self):
        self.calls += 1
        raise RuntimeError("broker unreachable")


def test_loop_trips_after_bounded_consecutive_failures(monkeypatch):
    from app.agent import live_loop as ll

    loop = ll.LiveLoop(_ExplodingPortal(), interval=1)
    # Force the loop to believe the market is open so it attempts a cycle.
    monkeypatch.setattr(ll.mh, "state", lambda: type(
        "S", (), {"is_open": True, "session": "regular",
                  "next_open": None, "next_close": None})())
    monkeypatch.setattr(loop._stop, "wait", lambda *_a, **_k: False)

    loop._run()

    assert loop.tripped is True
    assert loop.consecutive_errors == ll.MAX_CONSECUTIVE_ERRORS
    # The whole point: it stopped rather than retrying forever.
    assert loop.portal.calls == ll.MAX_CONSECUTIVE_ERRORS
    assert loop.status()["tripped"] is True


def test_an_explicit_start_rearms_the_breaker():
    from app.agent import live_loop as ll

    loop = ll.LiveLoop(_ExplodingPortal(), interval=1)
    loop.tripped = True
    loop.consecutive_errors = ll.MAX_CONSECUTIVE_ERRORS
    loop.start()
    try:
        assert loop.tripped is False
        assert loop.consecutive_errors == 0
    finally:
        loop.stop()


# --- trials: deflation and the cost of iteration --------------------------

def test_the_bar_rises_with_every_trial_examined():
    """The whole point: searching harder must make the winner mean less."""
    bars = [trials.expected_max_sharpe(n, 0.25) for n in (2, 10, 100, 1000)]
    assert bars == sorted(bars)
    assert bars[0] < bars[-1]


def test_a_single_trial_sets_no_selection_bar():
    assert trials.expected_max_sharpe(1, 0.25) == 0.0


def test_deflation_rejects_a_result_beaten_by_its_own_search():
    """Sharpe 1.0 after 200 looks is indistinguishable from the best coin."""
    d = trials.deflated_sharpe(observed_sr=1.0, n_periods=252, n_trials=200,
                               sharpe_variance=0.25)
    assert d["observed_sharpe"] < d["expected_max_sharpe_under_no_edge"]
    assert d["verdict"].startswith("REJECT")


def test_an_exceptional_result_still_survives_a_wide_search():
    d = trials.deflated_sharpe(observed_sr=1.8, n_periods=252, n_trials=200,
                               sharpe_variance=0.25)
    assert d["deflated_sharpe_ratio"] > 0.95
    assert d["verdict"].startswith("PASS")


def test_negative_skew_and_fat_tails_are_penalised():
    """The shape that wins small and often, then detonates.

    Measured on a SHORT track: over 252 periods a Sharpe of 1.5 saturates the
    normal CDF and both readings pin at 1.0, which hides a penalty that is
    still being applied. The correction is real; the probability scale just
    stops resolving it once the result is overwhelming either way.
    """
    clean = trials.probabilistic_sharpe(0.5, 0.0, 40, skew=0.0, kurtosis=3.0)
    ugly = trials.probabilistic_sharpe(0.5, 0.0, 40, skew=-1.5, kurtosis=9.0)
    assert ugly < clean
    assert 0.0 < ugly < 1.0


def test_trial_identity_separates_hypothesis_from_run():
    """One config across many folds is ONE hypothesis, not many."""
    a = trials.Trial(strategy="G", params={"x": 1}, sharpe=1.0, window="2023..2024")
    b = trials.Trial(strategy="G", params={"x": 1}, sharpe=1.1, window="2024..2025")
    c = trials.Trial(strategy="G", params={"x": 2}, sharpe=1.0, window="2023..2024")
    assert a.config_id == b.config_id       # same hypothesis, different window
    assert a.fingerprint != b.fingerprint   # but distinct runs
    assert a.config_id != c.config_id       # different params = new hypothesis


def test_sharpe_of_a_flat_curve_is_zero_not_an_error():
    assert trials.sharpe_of([0.0, 0.0, 0.0]) == 0.0
    assert trials.sharpe_of([]) == 0.0


# --- costs: friction that is never optional -------------------------------

def test_you_buy_at_the_ask_and_sell_at_the_bid():
    m = costs.CostModel.retail_equity()
    assert m.effective_buy_price(100.0) > 100.0
    assert m.effective_sell_price(100.0) < 100.0


def test_free_model_is_frictionless_for_reproducing_gross_numbers():
    m = costs.CostModel.free()
    assert m.effective_buy_price(100.0) == 100.0
    assert m.charge(10_000.0) == 0.0


def test_drag_is_linear_in_turnover():
    """Rebalance frequency is a cost decision before it is a signal decision."""
    m = costs.CostModel.retail_equity()
    assert costs.drag_per_year(m, 52) == pytest.approx(13 * costs.drag_per_year(m, 4))


def test_options_friction_dwarfs_equity_friction():
    eq = costs.CostModel.retail_equity().round_trip_bps()
    op = costs.CostModel.retail_option(1).round_trip_bps()
    assert op > 20 * eq
    # A single option round trip needs a multi-percent move just to break even.
    assert costs.CostModel.retail_option(1).breakeven_move_pct() > 2.0


def test_backtest_defaults_to_costed_not_gross():
    """Friction must be opt-OUT. Opt-in guarantees it is missing when it matters."""
    from app.backtest.engine import Backtest
    import inspect
    sig = inspect.signature(Backtest.__init__)
    assert "costs" in sig.parameters


# --- calibration: probabilities you can be wrong about --------------------

def test_a_constant_prediction_has_no_skill():
    outcomes = [1, 0] * 60
    r = calibration.evaluate([0.5] * 120, outcomes)
    assert abs(r.brier_skill) < 0.01
    assert r.verdict().startswith("NO SKILL")


def test_overconfidence_shows_up_as_calibration_error_not_brier():
    """The dangerous case: ranks fine, states confidence it has not earned."""
    true_p = [i / 100 for i in range(5, 96)]
    outcomes = [1 if p > 0.5 else 0 for p in true_p]
    honest = calibration.evaluate(true_p, outcomes)
    cocky = calibration.evaluate([min(0.99, max(0.01, 0.5 + (p - 0.5) * 2.5))
                                  for p in true_p], outcomes)
    assert cocky.ece < honest.ece or cocky.brier < honest.brier


def test_platt_scaler_maps_scores_monotonically_into_zero_one():
    s = calibration.PlattScaler(a=2.0, b=0.0)
    assert 0.0 < s.predict(-3.0) < s.predict(0.0) < s.predict(3.0) < 1.0
    assert s.predict(0.0) == pytest.approx(0.5)


def test_calibration_refuses_to_judge_a_tiny_sample():
    r = calibration.evaluate([0.6] * 5, [1, 0, 1, 1, 0])
    assert "too few" in r.verdict()


# --- meta-labeling: learning when to sit out ------------------------------

def test_meta_label_is_about_the_primary_being_right_not_the_return_sign():
    long_wrong = meta_label.MetaSample({}, primary_direction=1, forward_return=-0.02)
    short_right = meta_label.MetaSample({}, primary_direction=-1, forward_return=-0.02)
    assert long_wrong.label == 0
    assert short_right.label == 1     # same return, opposite label


def test_meta_model_can_refuse_which_the_primary_cannot():
    m = meta_label.MetaModel(weights={k: 0.0 for k in meta_label.FEATURES}, bias=-5.0)
    assert m.size({"abs_score": 1.0}) == 0.0


def test_meta_size_is_monotone_in_confidence():
    m = meta_label.MetaModel(weights={"abs_score": 4.0}, bias=-1.0)
    sizes = [m.size({"abs_score": x}) for x in (0.0, 0.5, 0.9, 1.0)]
    assert sizes == sorted(sizes)
    assert all(0.0 <= s <= 1.0 for s in sizes)


def test_meta_model_will_not_fit_on_too_little_data():
    samples = [meta_label.MetaSample({"abs_score": 0.5}, 1, 0.01) for _ in range(10)]
    m = meta_label.MetaModel().fit(samples)
    assert m.n_fit == 10
    assert m.weights == {}      # refused to learn


# --- the agent graph ------------------------------------------------------

def test_the_shipped_roster_satisfies_every_invariant():
    assert roster.validate() == []


def test_validator_catches_a_maker_that_consumes_a_checker(monkeypatch):
    """A validator that only ever passes is decoration. Prove it bites."""
    bad = roster.Agent(name="rogue", kind=roster.MAKER, leg=roster.SHARED,
                       owns="x", consumes=["thesis_ledger"])
    monkeypatch.setattr(roster, "ROSTER", roster.ROSTER + [bad])
    monkeypatch.setattr(roster, "BY_NAME", {**roster.BY_NAME, "rogue": bad})
    errs = roster.validate()
    assert any("circular judgement" in e for e in errs)


def test_validator_catches_a_maker_staging_an_irreversible_action(monkeypatch):
    bad = roster.Agent(name="rogue", kind=roster.MAKER, leg=roster.SHARED,
                       owns="x", stages=["place_order"])
    monkeypatch.setattr(roster, "ROSTER", roster.ROSTER + [bad])
    monkeypatch.setattr(roster, "BY_NAME", {**roster.BY_NAME, "rogue": bad})
    assert any("must not stage" in e for e in roster.validate())


def test_validator_catches_a_leg_reaching_into_the_other_leg(monkeypatch):
    """The exact coupling bug: an options checker consuming core research."""
    bad = roster.Agent(name="rogue_desk", kind=roster.CHECKER, leg=roster.OPTIONS,
                       owns="x", consumes=["macro_signals"])   # macro is CORE
    monkeypatch.setattr(roster, "ROSTER", roster.ROSTER + [bad])
    monkeypatch.setattr(roster, "BY_NAME", {**roster.BY_NAME, "rogue_desk": bad})
    assert any("legs must" in e for e in roster.validate())


def test_exactly_one_coordinator_owns_the_gate():
    coords = [a for a in roster.ROSTER if a.kind == roster.COORDINATOR]
    assert len(coords) == 1
    # and it gates every irreversible action, not a convenient subset
    assert set(autonomy.IRREVERSIBLE) <= set(coords[0].stages)


def test_no_checker_grades_without_an_independent_maker_upstream():
    for a in roster.ROSTER:
        if a.kind == roster.CHECKER and a.consumes:
            upstream = [roster.BY_NAME[d].kind for d in a.consumes if d in roster.BY_NAME]
            if upstream:
                assert roster.MAKER in upstream, f"{a.name} grades its own opinion"


def _with(agent, monkeypatch):
    monkeypatch.setattr(roster, "ROSTER", roster.ROSTER + [agent])
    monkeypatch.setattr(roster, "BY_NAME", {**roster.BY_NAME, agent.name: agent})
    return roster.validate()


def test_the_two_legs_run_on_different_clocks():
    """Collapsing both onto one 6 AM cadence is the public-setup mistake."""
    core = roster.BY_NAME["thesis_ledger"]
    opts = roster.BY_NAME["options_desk"]
    assert core.cadence_class in roster.SLOW_CADENCES
    assert opts.cadence_class in roster.FAST_CADENCES


def test_validator_rejects_a_core_checker_on_a_daily_clock(monkeypatch):
    bad = roster.Agent(name="daily_thesis", kind=roster.CHECKER, leg=roster.CORE,
                       owns="x", consumes=["filings_analyst"],
                       cadence_class="daily", signal_kind="verdict")
    assert any("manufactures activity" in e for e in _with(bad, monkeypatch))


def test_validator_rejects_an_options_checker_on_a_slow_clock(monkeypatch):
    bad = roster.Agent(name="monthly_desk", kind=roster.CHECKER, leg=roster.OPTIONS,
                       owns="x", consumes=["event_calendar"],
                       cadence_class="monthly", signal_kind="verdict")
    assert any("decays in days" in e for e in _with(bad, monkeypatch))


def test_validator_rejects_sentiment_on_the_core_leg(monkeypatch):
    """Mention volume is noise on a two-year hold; it belongs to the sleeve."""
    bad = roster.Agent(name="core_sentiment", kind=roster.MAKER, leg=roster.CORE,
                       owns="x", cadence_class="daily", signal_kind="sentiment")
    assert any("half-life" in e for e in _with(bad, monkeypatch))


def test_sentiment_is_allowed_on_the_options_leg(monkeypatch):
    """The same evidence is legitimate where it drives implied volatility."""
    ok = roster.Agent(name="iv_sentiment", kind=roster.MAKER, leg=roster.OPTIONS,
                      owns="x", cadence_class="daily", signal_kind="sentiment")
    assert _with(ok, monkeypatch) == []


def test_validator_rejects_a_self_improving_agent(monkeypatch):
    """The exact mechanism behind the overfit gap, refused structurally."""
    bad = roster.Agent(name="self_tuner", kind=roster.MAKER, leg=roster.SHARED,
                       owns="x", cadence_class="weekly", signal_kind="meta",
                       rewrites_own_rules=True)
    assert any("in-sample fitting" in e for e in _with(bad, monkeypatch))


def test_validator_rejects_an_agent_that_consumes_its_own_output(monkeypatch):
    bad = roster.Agent(name="ouroboros", kind=roster.MAKER, leg=roster.SHARED,
                       owns="x", consumes=["ouroboros"],
                       cadence_class="weekly", signal_kind="meta")
    assert any("consumes its own output" in e for e in _with(bad, monkeypatch))


def test_no_shipped_agent_rewrites_its_own_rules():
    assert not any(a.rewrites_own_rules for a in roster.ROSTER)


# --- onboarding: routing a new symbol into every layer --------------------

def test_readiness_is_reported_per_leg_not_as_one_flag():
    """A symbol is routinely usable by one leg and not the other."""
    r = onboard.Readiness(symbol="X", bars=onboard.MIN_BARS_TO_RANK_VOL)
    assert r.options_ready and not r.core_ready
    assert r.verdict() == onboard.READY_OPTIONS

    r2 = onboard.Readiness(symbol="Y", bars=10, has_fundamentals=True,
                           has_filings=True, has_thesis=True)
    assert r2.core_ready and not r2.options_ready
    assert r2.verdict() == onboard.READY_CORE


def test_measuring_vol_and_ranking_it_are_different_bars():
    """A level without its own distribution is not a signal."""
    assert onboard.MIN_BARS_TO_RANK_VOL > onboard.MIN_BARS_TO_MEASURE_VOL
    r = onboard.Readiness(symbol="X", bars=onboard.MIN_BARS_TO_RANK_VOL - 1)
    assert not r.options_ready


def test_a_symbol_with_nothing_is_reported_not_silently_accepted():
    assert onboard.Readiness(symbol="X").verdict() == onboard.NO_DATA


def test_untradeable_names_are_excluded_rather_than_reported_as_gaps():
    """A delisted position has no feed to fetch; listing it forever trains
    the reader to skim past this section."""
    from app.portfolio.holdings import UNTRADEABLE, is_untradeable
    assert is_untradeable("newyy")
    assert UNTRADEABLE["NEWYY"]                      # carries its reason
    assert not set(onboard.held_symbols()) & set(UNTRADEABLE)
    assert set(onboard.held_symbols(include_untradeable=True)) & set(UNTRADEABLE)


def test_missing_snapshots_name_the_exact_mcp_call_needed():
    """The portal cannot reach the MCP, so the handoff must be explicit."""
    r = onboard.assess("ZZZZ_NOT_A_TICKER")
    assert r.mcp_pulls
    assert any("get_equity_fundamentals" in p for p in r.mcp_pulls)
    assert any("get_sec_filing_index" in p for p in r.mcp_pulls)


# --- local LLM: the anti-hallucination gate -------------------------------

def test_verifier_accepts_a_value_present_in_the_source():
    src = "Cost of goods sold was $6,400 million."
    assert llm_extract.verify("6,400 million", src)
    assert llm_extract.verify("$6,400", src)      # formatting is not meaning
    assert llm_extract.verify("6400", src)


def test_verifier_rejects_a_number_that_is_not_in_the_source():
    """The whole safety property: an invented figure must not pass."""
    src = "Management declined to give a figure for 2027 bit supply growth."
    assert not llm_extract.verify("12%", src)
    assert not llm_extract.verify("9 days", src)


def test_verifier_rejects_empty_and_nonsense():
    assert not llm_extract.verify("", "anything")
    assert not llm_extract.verify("   ", "anything")


def test_verifier_is_not_fooled_by_a_digit_appearing_elsewhere():
    """'640' occurs inside '6,400' — a substring match on raw text would pass
    a wrong number. Normalisation must not make the gate permissive."""
    src = "Revenue was $6,400 million."
    assert llm_extract.verify("6,400", src)
    # A value the source does not state, whose digits are not contiguous there.
    assert not llm_extract.verify("4,006", src)


def test_extraction_reports_a_hallucination_rate_not_just_results():
    e = llm_extract.Extraction(fields={"a": "1"}, unverified={"b": "2"})
    assert e.to_dict()["hallucination_rate"] == 0.5
    assert e.to_dict()["verified_count"] == 1


def test_llm_is_declared_maker_only():
    """A verdict graded by a non-deterministic model is not falsifiable."""
    assert "MAKER only" in llm_extract.report()["role"]


# --- risk: caps that scale with the account -------------------------------

def _account(cash: float) -> Account:
    return Account(broker="paper", cash=cash, positions=[])


def test_caps_scale_down_with_a_small_account():
    """Percentage caps scale; the sleeve's flat cap then tightens the order."""
    L = RiskLimits()
    assert L.position_cap(184.0) == pytest.approx(18.4)      # 10% of equity
    assert L.order_cap(184.0) == pytest.approx(15.0)         # flat sleeve cap wins


def test_absolute_ceilings_still_bind_on_the_real_book():
    L = RiskLimits()
    assert L.order_cap(136_544.0) == 2000
    assert L.position_cap(136_544.0) == 5000


def test_a_full_pocket_takes_two_orders_under_the_flat_cap():
    """A stated consequence, not an accident.

    A $15 order cap against an $18.40 position cap means a full pocket is built
    in two orders. That is the operator's choice — a flat per-trade risk they
    picked — and it is far better than the three the old 4% cap required. Worth
    a test so the count cannot drift silently upward.
    """
    L = RiskLimits()
    pocket, order = L.position_cap(184.0), L.order_cap(184.0)
    assert order < pocket
    assert -(-pocket // order) == 2


def test_absolute_ceiling_still_binds_on_a_large_account():
    L = RiskLimits()
    assert L.order_cap(1_000_000.0) == 2000       # not 40,000
    assert L.position_cap(1_000_000.0) == 5000    # not 100,000


def test_percentage_cap_rejects_an_order_the_absolute_cap_would_have_allowed():
    L = RiskLimits()
    rm = RiskManager(L)
    acct = _account(184.0)
    order = Order(symbol="MRVL", side=Side.BUY, quantity=1)   # ~$222 notional
    decision = rm.approve(order, acct, ref_price=222.0)
    assert not decision.approved
    assert "order cap" in decision.reason


def test_small_order_within_the_scaled_cap_is_approved():
    rm = RiskManager(RiskLimits())
    acct = _account(184.0)
    order = Order(symbol="MRVL", side=Side.BUY, quantity=0.02)  # ~$4.44
    assert rm.approve(order, acct, ref_price=222.0).approved


def test_existing_holdings_count_toward_the_position_cap():
    rm = RiskManager(RiskLimits())
    acct = Account(broker="paper", cash=50_000.0,
                   positions=[Position(symbol="MRVL", quantity=20, avg_price=222.0)])
    order = Order(symbol="MRVL", side=Side.BUY, quantity=5)
    decision = rm.approve(order, acct, ref_price=222.0)
    assert not decision.approved
    assert "position cap" in decision.reason


# --- operator edge: scoring the human as a signal source ------------------

def test_beating_the_benchmark_is_the_test_not_going_up():
    """A +40% pick in a +50% market was a bad pick."""
    s = operator_edge.Scored("X", "2020-01-01", 63, entry=100, exit=140,
                             bench_entry=100, bench_exit=150)
    assert s.ret_pct == pytest.approx(40.0)
    assert s.excess_pct == pytest.approx(-10.0)
    assert not s.beat_benchmark


def test_outlier_carried_records_are_called_out_not_celebrated():
    """Positive mean with negative median is a different skill from selection."""
    rep = operator_edge.EdgeReport(horizon=63, total_buys=3)
    rep.scored = [
        operator_edge.Scored("W", "2021-01-01", 63, 100, 400, 100, 100),   # +300
        operator_edge.Scored("L", "2021-01-01", 63, 100, 90, 100, 100),    # -10
        operator_edge.Scored("L2", "2021-01-01", 63, 100, 92, 100, 100),   # -8
    ]
    s = rep.summary()
    assert s["mean_excess_pct"] > 0 > s["median_excess_pct"]
    assert "OUTLIERS" in s["verdict"]


def test_no_edge_is_reported_plainly():
    rep = operator_edge.EdgeReport(horizon=63, total_buys=2)
    rep.scored = [operator_edge.Scored("A", "2021-01-01", 63, 100, 95, 100, 110),
                  operator_edge.Scored("B", "2021-01-01", 63, 100, 98, 100, 105)]
    assert "NO EDGE" in rep.summary()["verdict"]


def test_unscoreable_buys_are_counted_never_silently_dropped():
    """Delisted names are overwhelmingly losers; hiding them biases everything up."""
    rep = operator_edge.score(63)
    assert rep.unscoreable
    assert sum(rep.unscoreable.values()) > 0
    assert rep.n < rep.total_buys


# --- peer-relative valuation: the operator's primary screen ---------------

def test_loss_makers_are_never_ranked_as_cheap():
    """A -45 P/E sorted numerically comes out 'cheapest'. That is backwards."""
    groups = {g.name: g for g in peer_value.build()}
    semis = groups["ai_semis"]
    assert "INTC" in semis.loss_making
    assert all(s.symbol != "INTC" for s in semis.standings)
    assert all(s.pe > 0 for s in semis.standings)


def test_standings_are_ordered_cheapest_first():
    for g in peer_value.build():
        pes = [s.pe for s in g.standings]
        assert pes == sorted(pes)
        assert [s.rank for s in g.standings] == list(range(1, len(pes) + 1))


def test_a_multiple_is_expressed_against_its_own_group():
    """23x is cheap for software and dear for peak-margin memory."""
    for g in peer_value.build():
        for s in g.standings:
            assert s.group_median_pe == g.median_pe
            assert s.vs_median_x == pytest.approx(s.pe / g.median_pe, abs=0.01)


def test_extremes_are_labelled_regardless_of_percentile():
    for g in peer_value.build():
        if len(g.standings) < 2:
            continue
        assert g.standings[0].band == peer_value.CHEAPEST
        assert g.standings[-1].band == peer_value.RICHEST


# --- source ripening: waiting for an early source -------------------------

def _store_with(tmp_path, days_old: int, source: str = "serenity"):
    """A real on-disk store — FeedStore reads from its file, not an attribute."""
    from datetime import date, timedelta
    import json
    from app.intel.feeds import FeedStore
    f = tmp_path / "feeds.jsonl"
    published = (date.today() - timedelta(days=days_old)).isoformat()
    row = {"source": source, "symbol": "ZZZ", "published": published,
           "bias": 0.8, "confidence": 0.9, "rationale": "t"}
    f.write_text(json.dumps(row) + "\n", encoding="utf-8")
    return FeedStore(path=f)


def test_an_unripe_view_is_ignored_not_weighted_down(tmp_path):
    """Serenity hit 25% at 30 days. Age-decay weighted her HEAVIEST there."""
    from app.intel.feeds import SOURCE_RIPEN_DAYS
    store = _store_with(tmp_path, SOURCE_RIPEN_DAYS["serenity"] - 10)
    assert store.bias("serenity", "ZZZ") is None


def test_a_ripened_view_counts_at_full_weight(tmp_path):
    from app.intel.feeds import SOURCE_RIPEN_DAYS
    store = _store_with(tmp_path, SOURCE_RIPEN_DAYS["serenity"] + 1)
    b = store.bias("serenity", "ZZZ")
    assert b is not None and b == pytest.approx(0.8, abs=0.05)


def test_ripening_does_not_start_a_view_already_half_decayed(tmp_path):
    """Decay must run from the END of ripening, or waiting costs weight."""
    from app.intel.feeds import SOURCE_RIPEN_DAYS
    just_ripe = _store_with(tmp_path, SOURCE_RIPEN_DAYS["serenity"] + 1)
    b = just_ripe.bias("serenity", "ZZZ")
    assert b > 0.75          # full strength, not halved by the 90-day wait


def test_sources_without_a_measured_ripen_behave_as_before(tmp_path):
    """Adding a source without measuring it must not silently trust it."""
    from app.intel.feeds import SOURCE_RIPEN_DAYS
    assert SOURCE_RIPEN_DAYS.get("personal", 0) == 0
    store = _store_with(tmp_path, 2, source="personal")
    assert store.bias("personal", "ZZZ") is not None


# --- small-sample guard on Sharpe -----------------------------------------

def test_sharpe_refuses_a_sample_too_small_to_support_one():
    """Two similar readings collapse the denominator and explode the ratio.

    A 2-fold backtest produced a 'Sharpe' of 12.15, which then became the
    registry's best result and dominated deflation. The number was 1/small.
    """
    tiny = [0.30, 0.31]
    assert trials.sharpe_of(tiny) == 0.0
    enough = [0.02, -0.01, 0.03, 0.00, 0.04, -0.02, 0.01, 0.02]
    assert len(enough) >= trials.MIN_OBSERVATIONS_FOR_SHARPE
    assert trials.sharpe_of(enough) != 0.0


def test_a_degenerate_config_still_counts_toward_the_trial_count():
    """It was examined, so N must include it — it just cannot win.

    Dropping it would shrink N and flatter every surviving result.
    """
    t = trials.Trial(strategy="x", params={"a": 1}, sharpe=trials.sharpe_of([0.3, 0.31]))
    assert t.sharpe == 0.0
    assert t.config_id


# --- discovery screen -----------------------------------------------------

def test_untagged_names_are_screened_not_hidden():
    """Gating on hand-curated themes would reproduce the miss it exists to fix."""
    rows = discovery.scan()
    assert any(c.theme == "untagged" for c in rows)
    for c in rows:
        if c.theme == "untagged":
            assert any("no theme mapping" in r for r in c.reasons)


def test_names_in_a_rotating_theme_are_excluded():
    from app.intel.themes import ALIVE
    for c in discovery.scan():
        if c.theme != "untagged":
            assert c.theme_state == ALIVE


def test_held_names_are_not_rediscovered():
    from app.portfolio.holdings import load_holdings
    held = {str(h["symbol"]).upper() for h in load_holdings()}
    assert not {c.symbol for c in discovery.scan()} & held


def test_missing_tier2_data_names_the_exact_pull():
    rows = [c for c in discovery.scan() if not c.has_peer_data]
    assert rows, "expected at least one candidate lacking peer data"
    assert any("get_equity_fundamentals" in p for p in rows[0].mcp_pulls)


# --- cycle x sector: measured, never authored -----------------------------

def test_current_year_is_excluded_from_its_own_median():
    """An incomplete year must not define the expectation it is judged against."""
    from datetime import date
    rows, cy = cycle_sectors.build()
    this_year = date.today().year
    for nc in rows:
        # the running year is held separately, never folded into a bucket
        assert nc.this_year is None or isinstance(nc.this_year, float)
        total = sum(len(v) for v in nc.by_cycle.values())
        assert total <= nc.n_years


def test_cycle_year_matches_the_macro_module():
    from app.macro.signals import cycle_year as macro_cy
    for y in (2024, 2025, 2026, 2027, 2028):
        assert cycle_sectors.cycle_year(y) == macro_cy(__import__("datetime").date(y, 6, 30))


def test_thin_samples_are_reported_as_insufficient():
    r = cycle_sectors.report()
    assert "observations_per_bucket" in r
    assert r["sufficient"] == (r["observations_per_bucket"] >= cycle_sectors.MIN_OBSERVATIONS)
    assert "n=" in r["verdict"]


def test_divergence_is_actual_minus_history_not_a_rating():
    nc = cycle_sectors.NameCycle(symbol="X", group="g", n_years=10, first_year=2016)
    nc.by_cycle = {2: [-10.0, -20.0, -30.0]}
    nc.this_year = 100.0
    assert nc.median_for(2) == -20.0
    assert nc.divergence(2) == 120.0


# --- chief: the decision schema is enforced, not described ----------------

def _dec(**kw):
    base = dict(id="t:1", subject="X", subject_type="symbol", kind=chief.RISK,
                urgency=chief.NOW, claim="c", falsifier="f", owner="o",
                action_verb="research",
                evidence=[chief.Evidence("src", "m", "v")])
    base.update(kw)
    return chief.Decision(**base)


def test_a_claim_without_a_falsifier_is_rejected():
    """An ungradeable claim is an opinion — the project's central discipline."""
    errs = chief.validate(_dec(falsifier="   "))
    assert any("falsifier" in e for e in errs)


def test_evidence_without_provenance_is_rejected():
    errs = chief.validate(_dec(evidence=[chief.Evidence("", "metric", "value")]))
    assert any("no source" in e for e in errs)


def test_an_irreversible_action_must_park():
    d = _dec(action_verb="place_order")
    assert d.gate[0] == "PARK"
    assert chief.validate(d) == []


def test_unknown_subject_type_is_rejected():
    assert any("subject_type" in e for e in chief.validate(_dec(subject_type="blob")))


def test_the_coordinator_generates_no_research_of_its_own():
    """Every decision must name a checker that produced it."""
    q = chief.build_queue()
    assert q["decisions"], "expected a non-empty queue"
    for d in q["decisions"]:
        assert d["owner"] and d["owner"] != "chief"


def test_the_live_queue_has_no_schema_errors():
    q = chief.build_queue()
    assert q["schema_errors"] == []
    assert q["collectors_failed"] == []


def test_a_failing_collector_is_reported_not_swallowed(monkeypatch):
    """A broken input must not look identical to a clean one."""
    def boom():
        raise RuntimeError("upstream down")
    monkeypatch.setattr(chief, "COLLECTORS", (boom,))
    q = chief.build_queue()
    assert q["collectors_failed"]
    assert "upstream down" in q["collectors_failed"][0]


def test_the_board_defers_where_a_specialist_exists():
    """Rolled-up and per-name versions of one finding would double the list."""
    q = chief.build_queue()
    board_subjects = {d["subject"] for d in q["decisions"] if d["owner"] == "statusboard"}
    assert not (board_subjects & chief.BOARD_DEFERS_TO_SPECIALIST)


# --- launching a dashboard is not consent to trade ------------------------

def test_daily_schedule_does_not_autostart_in_live_mode(monkeypatch):
    """launch.bat must not arm a 09:00 order job just by being run.

    In paper the 09:00 job is simulated and in confirm it parks, but in LIVE it
    would submit real orders unattended — which CLAUDE.md forbids outright.
    """
    from app.config import TradingMode
    from app.portal import Portal
    import app.portal as portal_mod

    started = []
    class _Sched:
        def __init__(self, *a, **k): pass
        def start(self): started.append(True)
        def stop(self): pass
        def status(self): return {}
    monkeypatch.setattr(portal_mod, "DailyScheduler", _Sched)

    p = Portal()
    monkeypatch.setattr(type(p.settings), "mode",
                        property(lambda self: TradingMode.LIVE), raising=False)
    p.build()
    try:
        assert started == [], "live mode must not autostart the order-placing schedule"
    finally:
        if getattr(p, "orchestrator", None):
            p.orchestrator.stop()
        if getattr(p, "live", None):
            p.live.stop()


# --- the test sleeve is a different activity, not a small book -----------

def test_a_flat_dollar_cap_applies_only_to_the_test_sleeve():
    """A percentage would drift the per-trade risk every time the balance moved.

    The operator picks what they are willing to lose per trade; the agent sizes
    freely underneath it.
    """
    L = RiskLimits()
    assert L.is_test_sleeve(184.0)
    assert L.order_cap(184.0) == pytest.approx(L.test_sleeve_max_order)
    # ...and the real book is untouched by it.
    assert not L.is_test_sleeve(136_544.0)
    assert L.order_cap(136_544.0) == 2000


def test_the_sleeve_cap_never_loosens_an_existing_limit():
    """It is a min(), so it can only ever tighten."""
    L = RiskLimits(test_sleeve_max_order=999_999)
    assert L.order_cap(184.0) <= 184.0 * L.max_order_pct


def test_the_agent_sizes_freely_under_the_cap():
    L = RiskLimits()
    rm = RiskManager(L)
    acct = Account(broker="paper", cash=184.0, positions=[])
    for notional, expect in ((5.0, True), (14.9, True), (15.5, False)):
        d = rm.approve(Order(symbol="TSLA", side=Side.BUY, quantity=notional / 410.0),
                       acct, 410.0)
        assert d.approved is expect, f"${notional} should be {'ok' if expect else 'rejected'}"
