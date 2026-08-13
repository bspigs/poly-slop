from poly_agent.models import Market, ProbabilityEstimate
from poly_agent.risk import decide_baseline_trade, decide_trade


def _estimate(fair_yes: float, confidence: float) -> ProbabilityEstimate:
    return ProbabilityEstimate(
        fair_yes_probability=fair_yes,
        confidence=confidence,
        thesis="test",
        strongest_yes_evidence=["a"],
        strongest_no_evidence=["b"],
        key_uncertainties=["c"],
        resolution_risk="low",
    )


def test_yes_trade_when_edge_is_large():
    market = Market(id="1", question="Test?", yes_price=0.40, no_price=0.60, liquidity=100000, volume=100000)
    d = decide_trade(market, _estimate(0.58, 0.8), bankroll=10000)
    assert d.side == "YES"
    assert d.edge > 0.1
    assert 0 < d.stake <= 100


def test_pass_when_confidence_low():
    market = Market(id="1", question="Test?", yes_price=0.40, no_price=0.60, liquidity=100000, volume=100000)
    d = decide_trade(market, _estimate(0.70, 0.3), bankroll=10000)
    assert d.side == "PASS"
    assert d.stake == 0


def test_baseline_executes_even_when_normal_mode_passes():
    market = Market(id="1", question="Test?", yes_price=0.49, no_price=0.51, liquidity=100000, volume=100000)
    estimate = _estimate(0.51, 0.30)

    normal = decide_trade(market, estimate, bankroll=10000)
    baseline = decide_baseline_trade(market, estimate, bankroll=10000)

    assert normal.side == "PASS"
    assert baseline.side == "YES"
    assert baseline.stake == 25.00
