from datetime import datetime, timedelta, timezone

from poly_agent.config import Settings
from poly_agent.models import Market
from poly_agent.scanner import eligible, rank_markets


def _market(market_id: str, end_date, liquidity=100000, volume=100000) -> Market:
    return Market(
        id=market_id,
        question=f"Market {market_id}?",
        yes_price=0.50,
        no_price=0.50,
        liquidity=liquidity,
        volume=volume,
        end_date=end_date,
    )


def test_live_filter_keeps_only_markets_within_one_week():
    now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    settings = Settings(min_days_to_resolution=0, max_days_to_resolution=7)

    soon = _market("soon", now + timedelta(days=2))
    far = _market("far", now + timedelta(days=20))
    missing = _market("missing", None)

    assert eligible(soon, settings, now=now)
    assert not eligible(far, settings, now=now)
    assert not eligible(missing, settings, now=now)


def test_nearer_market_ranks_ahead_when_other_inputs_match():
    now = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
    settings = Settings(min_days_to_resolution=0, max_days_to_resolution=7)

    one_day = _market("one-day", now + timedelta(days=1))
    six_days = _market("six-days", now + timedelta(days=6))

    ranked = rank_markets([six_days, one_day], settings, now=now)
    assert [m.id for m in ranked] == ["one-day", "six-days"]


def test_demo_mode_can_ignore_resolution_window():
    settings = Settings(min_days_to_resolution=0, max_days_to_resolution=7)
    demo = _market("demo", None)
    assert eligible(demo, settings, enforce_resolution_window=False)
