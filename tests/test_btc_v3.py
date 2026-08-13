from datetime import datetime, timedelta, timezone
from dataclasses import replace

from poly_agent import btc_v3
from poly_agent.btc_signal import BookSnapshot
from poly_agent.btc_v3 import MakerQuote, _maker_bid_filled, _maker_candidate, _pair_arb
from poly_agent.config import SETTINGS
from poly_agent.models import Market


def _market() -> Market:
    return Market(
        id="v3-test",
        question="Bitcoin Up or Down - test",
        slug="btc-updown-15m-test",
        end_date=datetime.now(timezone.utc) + timedelta(minutes=5),
        yes_price=0.50,
        no_price=0.50,
        positive_label="UP",
        negative_label="DOWN",
        positive_token_id="up-token",
        negative_token_id="down-token",
    )


def test_pair_arb_requires_profit_after_both_taker_fees(monkeypatch):
    monkeypatch.setattr(btc_v3, "v3_equity", lambda s=SETTINGS: 10_000.0)
    settings = replace(SETTINGS, btc_v3_arb_min_profit_per_pair=0.001)
    # 0.45 + 0.45 leaves enough room to pay both crypto taker fees and retain profit.
    up = BookSnapshot(bid=0.44, ask=0.45, bid_size=500, ask_size=500)
    down = BookSnapshot(bid=0.44, ask=0.45, bid_size=500, ask_size=500)
    result = _pair_arb(_market(), up, down, s=settings)
    assert result is not None
    trade, _ = result
    assert trade.realized_pnl is not None and trade.realized_pnl > 0
    assert trade.entry_fee > 0
    assert trade.strategy == "PAIR_ARB"


def test_pair_arb_rejects_normal_one_dollar_combined_asks(monkeypatch):
    monkeypatch.setattr(btc_v3, "v3_equity", lambda s=SETTINGS: 10_000.0)
    up = BookSnapshot(bid=0.49, ask=0.50, bid_size=500, ask_size=500)
    down = BookSnapshot(bid=0.49, ask=0.50, bid_size=500, ask_size=500)
    assert _pair_arb(_market(), up, down, s=SETTINGS) is None


def test_maker_candidate_improves_bid_without_crossing():
    settings = replace(SETTINGS, btc_v3_maker_min_edge=0.02, btc_v3_min_spread_ticks=2.0)
    up = BookSnapshot(bid=0.45, ask=0.48, bid_size=500, ask_size=500)
    down = BookSnapshot(bid=0.52, ask=0.55, bid_size=500, ask_size=500)
    quote = _maker_candidate(_market(), up, down, 0.60, {}, s=settings)
    assert quote is not None
    assert quote.side == "YES"
    assert quote.price == 0.46
    assert quote.price < up.ask
    assert quote.edge > 0


def test_maker_candidate_skips_one_tick_spread():
    settings = replace(SETTINGS, btc_v3_maker_min_edge=0.0, btc_v3_min_spread_ticks=2.0)
    up = BookSnapshot(bid=0.49, ask=0.50, bid_size=500, ask_size=500)
    down = BookSnapshot(bid=0.50, ask=0.51, bid_size=500, ask_size=500)
    assert _maker_candidate(_market(), up, down, 0.70, {}, s=settings) is None


def test_maker_fill_requires_new_sell_trade_at_or_through_quote():
    quote = MakerQuote(
        market_id="v3-test",
        side="YES",
        side_label="UP",
        token_id="up-token",
        price=0.46,
        fair_probability=0.60,
        edge=0.14,
        tick_size=0.01,
        placed_at=datetime.now(timezone.utc),
        baseline_last_trade=(0.47, "BUY"),
    )
    assert not _maker_bid_filled(quote, {"up-token": (0.47, "BUY")})
    assert not _maker_bid_filled(quote, {"up-token": (0.47, "SELL")})
    assert _maker_bid_filled(quote, {"up-token": (0.46, "SELL")})
    assert _maker_bid_filled(quote, {"up-token": (0.45, "SELL")})
