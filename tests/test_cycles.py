from datetime import date
from app.macro import signals as S


def test_halving_contributes_no_bias():
    assert S.halving_cycle(date(2026, 9, 7)).value == 0.0


def test_halving_is_excluded_from_the_blend_not_averaged_in_as_zero():
    """A 0.0 term is not neutral — averaging it in drags the blend toward zero
    and silently weakens every real signal. Adding the halving naively would
    have cut the existing macro tilt by a third."""
    d = date(2026, 9, 7)
    sigs = [s for s in S.all_signals(d, with_m2=False)]
    assert any(s.name == "halving_cycle" for s in sigs)
    contributing = [s for s in sigs if s.name not in S.NON_CONTRIBUTING]
    expected = sum(s.value for s in contributing) / len(contributing)
    assert S.blended_bias(d, with_m2=False) == expected
    # and it must equal the presidential signal alone, undiluted
    assert S.blended_bias(d, with_m2=False) == S.presidential_cycle(d).value


def test_quartiles_span_the_cycle():
    assert S.halving_quartile(date(2024, 5, 1)) == 1
    assert S.halving_quartile(date(2026, 9, 7)) == 3
    assert S.halving_quartile(date(2028, 3, 1)) == 4


def test_quartile_never_escapes_one_to_four():
    for y in range(2013, 2029):
        for m in (1, 6, 11):
            assert 1 <= S.halving_quartile(date(y, m, 15)) <= 4


def test_halving_bounds_pick_the_surrounding_events():
    last, nxt, idx = S._halving_bounds(date(2026, 9, 7))
    assert last.isoformat() == "2024-04-19"
    assert nxt.isoformat() == S.NEXT_HALVING_EST
    assert idx == 4


def test_detail_states_the_sample_problem():
    # The caveat must travel with the number, not live in a docstring.
    assert "n=3" in S.halving_cycle(date(2026, 9, 7)).detail


def test_current_cycle_peak_is_reported_as_passed_not_upcoming():
    """The 2024 cycle peaked at +535d. Presenting that as an upcoming event
    would be the most misleading thing this function could do."""
    p = S.halving_projection(date(2026, 9, 7))
    assert p["current_cycle"]["peak_already_passed"] is True
    assert "trough_window" in p["current_cycle"]


def test_peak_window_contains_the_actual_measured_peak():
    # Actual 2024-cycle peak was 2025-10-06; the window is 2025-09-26..10-17.
    p = S.halving_projection(date(2026, 9, 7))
    lo, hi = p["current_cycle"]["peak_window"]
    assert lo <= "2025-10-06" <= hi


def test_next_peak_is_compared_against_the_campaign_deadline():
    p = S.halving_projection(date(2026, 9, 7))
    vs = p["vs_campaign"]
    assert vs["deadline"] == "2027-12-31"
    assert vs["next_peak_after_deadline_days"] > 0
    assert "offers the campaign nothing" in vs["verdict"]


def test_projection_states_its_sample_size_everywhere():
    p = S.halving_projection(date(2026, 9, 7))
    assert p["peak_offset_days"]["n"] == 3
    assert p["current_cycle"]["trough_window_n"] == 2
    assert "n=3" in p["caveat"]


def test_before_the_peak_window_no_trough_is_projected():
    # Shortly after a halving the peak has not passed, so projecting a trough
    # from it would be inventing a date from a date that does not exist yet.
    p = S.halving_projection(date(2024, 8, 1))
    assert p["current_cycle"]["peak_already_passed"] is False
    assert "trough_window" not in p["current_cycle"]
