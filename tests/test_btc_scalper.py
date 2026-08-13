from datetime import datetime, timedelta, timezone

import pytest

from poly_agent.btc_scalper import ScalpTrade, net_exit_pnl, scalp_metrics, taker_fee


def _trade(entry: float = 0.50, stake: float = 25.0) -> ScalpTrade:
    shares = stake / entry
    entry_fee = taker_fee(shares, entry)
    now = datetime.now(timezone.utc)
    return ScalpTrade(
        market_id="btc-1",
        question="Bitcoin Up or Down - test",
        side="YES",
        side_label="UP",
        token_id="token-up",
        entry_time=now,
        window_end=now + timedelta(minutes=15),
        entry_price=entry,
        stake=stake,
        shares=shares,
        entry_fee=entry_fee,
        model_fair_probability=0.55,
        model_confidence=0.5,
        peak_exit_bid=entry,
        peak_net_pnl=-entry_fee,
    )


def test_crypto_fee_matches_documented_formula():
    assert taker_fee(50, 0.50) == pytest.approx(0.875)


def test_tiny_gross_move_can_still_be_net_loss_after_fees():
    trade = _trade(entry=0.50)
    net, exit_fee = net_exit_pnl(trade, 0.52)
    assert exit_fee > 0
    assert net < 0


def test_larger_move_can_clear_round_trip_fees():
    trade = _trade(entry=0.50)
    net, _ = net_exit_pnl(trade, 0.55)
    assert net > 0


def test_scalp_metrics_use_realized_net_pnl():
    winner = _trade().model_copy(
        update={
            "trade_id": "win",
            "status": "CLOSED",
            "exit_time": datetime.now(timezone.utc),
            "exit_price": 0.56,
            "exit_fee": 0.8,
            "realized_pnl": 0.75,
        }
    )
    loser = _trade().model_copy(
        update={
            "trade_id": "loss",
            "status": "CLOSED",
            "exit_time": datetime.now(timezone.utc),
            "exit_price": 0.45,
            "exit_fee": 0.8,
            "realized_pnl": -2.0,
        }
    )
    metrics = scalp_metrics([winner, loser])
    assert metrics["wins"] == 1
    assert metrics["losses"] == 1
    assert metrics["hit_rate"] == pytest.approx(0.5)
    assert metrics["net_pnl"] == pytest.approx(-1.25)
