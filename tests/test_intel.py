from app.intel.events import Event, EventCategory, Sentiment
from app.intel.geopolitics import GeopoliticalEngine
from app.intel.grok import GrokClient
from app.intel.sentiment import score_text
from app.intel.service import IntelService


def test_sentiment_directions():
    assert score_text("Company beats estimates, shares surge to record high") > 0.3
    assert score_text("Stock plunges after fraud probe and downgrade") < -0.3
    assert abs(score_text("The market was open today")) < 0.2


def test_sentiment_negation():
    pos = score_text("earnings beat expectations")
    neg = score_text("earnings did not beat expectations")
    assert pos > 0 and neg < pos


def test_grok_mock_mode_returns_events():
    g = GrokClient(api_key="")           # force mock
    assert not g.live
    events = g.fetch_events(focus=["NVDA"])
    assert events and all(isinstance(e, Event) for e in events)
    assert any("NVDA" in e.symbols for e in events)


def test_geopolitical_engine_flags_conflict_risk_off():
    eng = GeopoliticalEngine()
    ev = Event(headline="Missile strike escalates military conflict in the region",
               source="mock", category=EventCategory.GEOPOLITICAL, importance=0.9)
    assessment = eng.assess([ev])
    assert assessment.risk_tilt == "risk_off"
    assert "armed_conflict" in assessment.matched_themes
    # defense/energy proxies should lean bullish, broad equity bearish
    assert assessment.ticker_bias.get("ITA", 0) > 0
    assert assessment.ticker_bias.get("SPY", 0) < 0


def test_geopolitical_de_escalation_risk_on():
    eng = GeopoliticalEngine()
    ev = Event(headline="Ceasefire and peace deal reached, sanctions lifted",
               source="mock", category=EventCategory.GEOPOLITICAL, importance=0.8)
    a = eng.assess([ev])
    assert a.risk_tilt == "risk_on"


def test_intel_service_briefing_and_signals():
    svc = IntelService(grok=GrokClient(api_key=""))   # mock
    brief = svc.briefing(["NVDA", "SMH"])
    assert "geo" in brief.to_dict()
    assert isinstance(brief.symbol_sentiment, dict)
    # NVDA is mildly positive (earnings beat) but diluted by the tariff risk;
    # SMH is clearly negative from the semiconductor-tariff event.
    assert brief.symbol_sentiment["NVDA"] > 0
    assert brief.symbol_sentiment["SMH"] < -0.2
    # The tariff event should surface a bearish SMH signal.
    sigs = svc.signals_for(["NVDA", "SMH"], threshold=0.3)
    assert any(s.symbol == "SMH" and s.side.value == "sell" for s in sigs)
