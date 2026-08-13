import json

from poly_agent.config import Settings
from poly_agent.models import Market
from poly_agent.research import estimate_probability, resolve_provider


def test_auto_provider_prefers_ollama_without_openai_key():
    assert resolve_provider("auto", Settings(openai_api_key=None)) == "ollama"


def test_auto_provider_prefers_openai_with_key():
    assert resolve_provider("auto", Settings(openai_api_key="test")) == "openai"


def test_ollama_structured_response(monkeypatch):
    body = {
        "fair_yes_probability": 0.61,
        "confidence": 0.55,
        "thesis": "Test thesis",
        "strongest_yes_evidence": ["yes"],
        "strongest_no_evidence": ["no"],
        "key_uncertainties": ["uncertain"],
        "resolution_risk": "low",
    }

    class FakeResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"message": {"content": json.dumps(body)}}

    def fake_post(*args, **kwargs):
        assert kwargs["json"]["format"]["type"] == "object"
        assert kwargs["json"]["stream"] is False
        return FakeResponse()

    monkeypatch.setattr("poly_agent.research.requests.post", fake_post)
    market = Market(id="1", question="Test?", yes_price=0.5, no_price=0.5)
    estimate = estimate_probability(market, provider="ollama", s=Settings())
    assert estimate.fair_yes_probability == 0.61
