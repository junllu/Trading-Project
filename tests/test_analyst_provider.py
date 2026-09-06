from app.intel.analyst import OllamaAnalyst, build_analyst
from app.intel.claude_analyst import ClaudeAnalyst


def test_factory_returns_expected_providers():
    assert isinstance(build_analyst("heuristic"), ClaudeAnalyst)
    assert isinstance(build_analyst("ollama"), OllamaAnalyst)
    assert isinstance(build_analyst("anthropic"), ClaudeAnalyst)
    assert isinstance(build_analyst(None), ClaudeAnalyst)   # default heuristic


def test_ollama_falls_back_to_heuristic_when_server_down():
    # No Ollama server here -> analyze() must degrade to the heuristic, not crash.
    a = OllamaAnalyst(model="llama3.1", host="http://127.0.0.1:1")   # nothing listening
    res = a.analyze([{"symbol": "AAA", "technical": 0.7, "rsi": 60}])
    assert res.source == "heuristic"
    assert "AAA" in res.ratings


def test_heuristic_provider_offline():
    a = build_analyst("heuristic")
    assert not a.live
    res = a.analyze([{"symbol": "MRVL", "technical": -0.3, "rsi": 40}])
    assert res.ratings["MRVL"] < 0
