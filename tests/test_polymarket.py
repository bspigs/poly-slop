from datetime import datetime, timezone

from poly_agent.polymarket import btc_15m_slug, normalize_market


def test_normalize_gamma_market():
    raw = {
        "id": "abc",
        "question": "Will X happen?",
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.42", "0.58"]',
        "clobTokenIds": '["yes-token", "no-token"]',
        "liquidityNum": 50000,
        "volumeNum": 100000,
        "active": True,
        "closed": False,
    }
    market = normalize_market(raw)
    assert market is not None
    assert market.yes_price == 0.42
    assert market.no_price == 0.58
    assert market.positive_label == "YES"
    assert market.negative_label == "NO"
    assert market.positive_token_id == "yes-token"
    assert market.negative_token_id == "no-token"


def test_normalize_btc_up_down_market():
    raw = {
        "id": "btc-test",
        "question": "Bitcoin Up or Down - test window",
        "slug": "btc-updown-15m-1786588200",
        "outcomes": '["Up", "Down"]',
        "outcomePrices": '["0.57", "0.43"]',
        "clobTokenIds": '["up-token", "down-token"]',
        "liquidityNum": 25000,
        "volumeNum": 50000,
        "active": True,
        "closed": False,
    }
    market = normalize_market(raw)
    assert market is not None
    assert market.yes_price == 0.57
    assert market.no_price == 0.43
    assert market.positive_label == "UP"
    assert market.negative_label == "DOWN"
    assert market.positive_token_id == "up-token"
    assert market.negative_token_id == "down-token"


def test_btc_15m_slug_floors_to_current_window():
    now = datetime(2026, 8, 13, 2, 39, 10, tzinfo=timezone.utc)
    slug, start, end = btc_15m_slug(now)
    assert slug == "btc-updown-15m-1786588200"
    assert start == datetime(2026, 8, 13, 2, 30, tzinfo=timezone.utc)
    assert end == datetime(2026, 8, 13, 2, 45, tzinfo=timezone.utc)
