from datetime import datetime, timezone

import pytest

from poly_agent.models import Market, PaperPosition
from poly_agent.report import resolved_winner, settle_position


def _position(side: str, entry: float, fair: float, stake: float = 25.0) -> PaperPosition:
    return PaperPosition(
        timestamp=datetime.now(timezone.utc),
        market_id="1",
        question="Test?",
        side=side,
        entry_price=entry,
        fair_probability=fair,
        confidence=0.5,
        stake=stake,
        shares=stake / entry,
        estimated_edge=0.0,
    )


def _market(yes: float, no: float, closed: bool = True) -> Market:
    return Market(
        id="1",
        question="Test?",
        yes_price=yes,
        no_price=no,
        liquidity=100000,
        volume=100000,
        active=not closed,
        closed=closed,
    )


def test_resolved_winner_requires_closed_one_zero_market():
    assert resolved_winner(_market(1.0, 0.0, True)) == "YES"
    assert resolved_winner(_market(0.0, 1.0, True)) == "NO"
    assert resolved_winner(_market(0.7, 0.3, True)) is None
    assert resolved_winner(_market(1.0, 0.0, False)) is None


def test_winning_yes_position_pnl_and_brier():
    position = _position("YES", entry=0.50, fair=0.70)
    result = settle_position(position, _market(1.0, 0.0))

    assert result.status == "WIN"
    assert result.pnl == pytest.approx(25.0)
    assert result.brier == pytest.approx(0.09)


def test_losing_no_position_pnl_and_yes_brier_conversion():
    position = _position("NO", entry=0.40, fair=0.65)
    result = settle_position(position, _market(1.0, 0.0))

    assert result.status == "LOSS"
    assert result.pnl == pytest.approx(-25.0)
    # fair_probability is for the selected NO side, so fair YES is 0.35.
    assert result.brier == pytest.approx((0.35 - 1.0) ** 2)
