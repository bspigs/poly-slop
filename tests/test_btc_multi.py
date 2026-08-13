from datetime import datetime, timedelta, timezone

from poly_agent import btc_multi
from poly_agent.btc_scalper import ScalpTrade


def _trade(status: str, trade_id: str) -> ScalpTrade:
    now = datetime.now(timezone.utc)
    return ScalpTrade(
        trade_id=trade_id,
        market_id="btc-window",
        question="Bitcoin Up or Down?",
        side="YES",
        side_label="UP",
        token_id="token-up",
        entry_time=now,
        window_end=now + timedelta(minutes=10),
        entry_price=0.50,
        stake=25.0,
        shares=50.0,
        entry_fee=0.50,
        model_fair_probability=0.55,
        model_confidence=0.50,
        peak_exit_bid=0.51,
        peak_net_pnl=0.10,
        status=status,
        exit_time=now if status == "CLOSED" else None,
        exit_price=0.52 if status == "CLOSED" else None,
        realized_pnl=0.25 if status == "CLOSED" else None,
    )


def test_closed_trade_does_not_block_same_window_reentry(monkeypatch):
    monkeypatch.setattr(btc_multi, "load_scalps", lambda: [_trade("CLOSED", "a")])
    assert btc_multi._open_trade_for_market("btc-window") is None
    assert btc_multi._trade_count_for_market("btc-window") == 1


def test_open_trade_is_detected_for_resume(monkeypatch):
    trades = [_trade("CLOSED", "a"), _trade("OPEN", "b")]
    monkeypatch.setattr(btc_multi, "load_scalps", lambda: trades)
    found = btc_multi._open_trade_for_market("btc-window")
    assert found is not None
    assert found.trade_id == "b"
    assert btc_multi._trade_count_for_market("btc-window") == 2
