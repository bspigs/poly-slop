from poly_agent.polymarket import normalize_market


def test_normalize_gamma_market():
    raw = {
        "id": "abc",
        "question": "Will X happen?",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.42", "0.58"]',
        "liquidityNum": 50000,
        "volumeNum": 100000,
        "active": True,
        "closed": False,
    }
    market = normalize_market(raw)
    assert market is not None
    assert market.yes_price == 0.42
    assert market.no_price == 0.58
