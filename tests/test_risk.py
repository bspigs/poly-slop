from poly_agent.models import Market, ProbabilityEstimate
from poly_agent.risk import decide_trade


def test_yes_trade_when_edge_is_large():
    market = Market(id="1", question="Test?", yes_price=0.40, no_price=0.60, liquidity=100000, volume=100000)
    estimate = ProbabilityEstimate(
        fair_yes_probability=0.58,
        confidence=0.8,
        thesis="test",
        strongest_yes_evidence=["a"],
        strongest_no_evidence=["b"],
        key_uncertainties=["c"],
        resolution_risk="low",
    )
    d = decide_trade(market, estimate, bankroll=10000)
    assert d.side == "YES"
    assert d.edge > 0.1
    assert 0 < d.stake <= 100


def test_pass_when_confidence_low():
    market = Market(id="1", question="Test?", yes_price=0.40, no_price=0.60, liquidity=100000, volume=100000)
    estimate = ProbabilityEstimate(
        fair_yes_probability=0.70,
        confidence=0.3,
        thesis="test",
        strongest_yes_evidence=[],
        strongest_no_evidence=[],
        key_uncertainties=[],
        resolution_risk="high",
    )
    d = decide_trade(market, estimate, bankroll=10000)
    assert d.side == "PASS"
    assert d.stake == 0
