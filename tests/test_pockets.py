from app.portfolio.pockets import Candidate, PocketAllocator, DEFAULT_POCKETS


def test_ladder_sums_to_80k():
    assert sum(DEFAULT_POCKETS) == 80000


def test_core_goes_to_high_conviction_gems_to_high_upside():
    cands = [
        Candidate("AAA", conviction=0.8, upside=0.05, risk=0.3),   # core
        Candidate("BBB", conviction=0.6, upside=0.04, risk=0.3),   # core
        Candidate("CCC", conviction=0.05, upside=0.30, risk=0.9),  # gem (low conv, huge upside)
        Candidate("DDD", conviction=0.05, upside=0.20, risk=0.9),  # gem
        Candidate("EEE", conviction=-0.2, upside=0.01, risk=0.2),  # nothing
    ]
    plan = PocketAllocator([30000, 20000, 1000, 1000], total=52000,
                           gem_threshold=2000).allocate(cands).to_dict()
    core_syms = [a["symbol"] for a in plan["core"]]
    gem_syms = [a["symbol"] for a in plan["gems"]]
    assert core_syms == ["AAA", "BBB"]                 # biggest pockets, highest conviction
    assert set(gem_syms) == {"CCC", "DDD"}             # $1k pockets, highest upside
    # pocket sizes assigned largest-first
    assert plan["core"][0]["target_value"] == 30000
    assert plan["core"][1]["target_value"] == 20000


def test_unfilled_pockets_stay_cash():
    cands = [Candidate("AAA", conviction=0.8, upside=0.05, risk=0.3)]
    plan = PocketAllocator([30000, 20000, 1000], total=51000, gem_threshold=2000).allocate(cands).to_dict()
    assert plan["invested"] == 30000                   # only one worthy name
    assert plan["cash_unallocated"] == 21000


def test_risk_adjustment_prefers_lower_risk_when_conviction_ties():
    cands = [
        Candidate("SAFE", conviction=0.6, upside=0.05, risk=0.1),
        Candidate("WILD", conviction=0.6, upside=0.05, risk=0.9),
    ]
    plan = PocketAllocator([30000, 20000], total=50000, gem_threshold=2000).allocate(cands).to_dict()
    # lower-risk name gets the bigger pocket
    assert plan["core"][0]["symbol"] == "SAFE"
    assert plan["core"][0]["target_value"] == 30000
