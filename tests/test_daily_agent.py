from app.agent.daily import DailyAgent
from app.config import Settings, TradingMode
from app.intel.claude_analyst import ClaudeAnalyst
from app.portal import Portal


def build_portal():
    raw = {
        "brokers": {"paper": {"enabled": True, "starting_cash": 100000}},
        "watchlist": ["AAA", "BBB"],
        "risk": {"max_order_value": 5000, "max_position_value": 50000, "allowed_symbols_only": False},
        "strategies": [],
        "agent": {"execute": True, "sizing": {"base_budget": 1000, "entry_threshold": 0.2}},
    }
    p = Portal(settings=Settings(mode=TradingMode.PAPER, raw=raw)).build()
    # Ignore any real holdings.yaml on disk so the test universe is just AAA/BBB.
    p.held_symbols = []
    # Point the campaign's focus at the test symbols so BUY concentration allows them.
    p.campaign.focus_symbols = ["AAA", "BBB"]
    # Ignore any real holdings.yaml on disk and give the paper test real buying power.
    paper = p.brokers["paper"]
    paper._positions.clear()
    paper.cash = 100_000.0
    # Re-anchor the campaign to the fresh book so no artificial drawdown trips.
    p.campaign.start_capital = paper.cash
    p.campaign.high_water_mark = paper.cash
    # seed a clean uptrend and downtrend so technicals are decisive
    p.market.seed_history("AAA", [100 + i for i in range(60)])
    p.market.seed_history("BBB", [160 - i for i in range(60)])
    p.brokers["paper"].set_price("AAA", 159.0)
    p.brokers["paper"].set_price("BBB", 101.0)
    return p


def test_claude_analyst_heuristic_offline():
    a = ClaudeAnalyst(api_key="")
    assert not a.live
    res = a.analyze([{"symbol": "AAA", "technical": 0.7, "rsi": 60}])
    assert "AAA" in res.ratings
    assert res.source == "heuristic"


def test_daily_agent_produces_report_and_convictions():
    p = build_portal()
    p.analyst = ClaudeAnalyst(api_key="")  # force heuristic for a deterministic test
    p.daily_agent = DailyAgent(p, analyst=p.analyst, conviction=p.conviction, execute=True)
    report = p.daily_agent.run()
    d = report.to_dict()
    assert d["mode"] == "paper"
    assert d["convictions"]
    syms = {c["symbol"]: c for c in d["convictions"]}
    # uptrend name should outrank downtrend name
    assert syms["AAA"]["score"] > syms["BBB"]["score"]
    assert "summary" in d and d["summary"]


def test_daily_agent_executes_orders_in_paper():
    p = build_portal()
    p.analyst = ClaudeAnalyst(api_key="")
    p.daily_agent = DailyAgent(p, analyst=p.analyst, conviction=p.conviction, execute=True)
    report = p.daily_agent.run()
    # the strong uptrend should have generated at least one actioned order
    actions = report.to_dict()["actions"]
    assert any(a["symbol"] == "AAA" for a in actions)


def test_daily_agent_respects_execute_flag():
    p = build_portal()
    p.analyst = ClaudeAnalyst(api_key="")
    agent = DailyAgent(p, analyst=p.analyst, conviction=p.conviction, execute=False)
    report = agent.run()
    assert report.to_dict()["actions"] == []
