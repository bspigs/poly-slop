from dataclasses import replace
from datetime import datetime, timedelta, timezone

from poly_agent import btc_tick
from poly_agent.btc_signal import BookSnapshot, SpotSample
from poly_agent.btc_tick import BtcTickEngine
from poly_agent.config import SETTINGS
from poly_agent.models import Market


class FakeFeed:
    def __init__(self, price=100_050.0, m15=None, m30=None):
        self.now = datetime.now(timezone.utc)
        self.price = price
        self.m15 = m15
        self.m30 = m30

    def sample(self):
        return SpotSample(self.now, self.price, self.price - 1, self.price + 1)

    def window_open_price(self, start):
        return 100_000.0

    def sigma_per_sqrt_second(self):
        return 0.000025

    def momentum_bps(self, seconds):
        if seconds == 15:
            return self.m15
        if seconds == 30:
            return self.m30
        return None


def _market(end):
    return Market(
        id="tick-test",
        question="Bitcoin Up or Down - tick test",
        end_date=end,
        yes_price=0.50,
        no_price=0.50,
        positive_label="UP",
        negative_label="DOWN",
        positive_token_id="up-token",
        negative_token_id="down-token",
    )


def _settings():
    return replace(
        SETTINGS,
        btc_tick_min_fee_adjusted_edge=-1.0,
        btc_tick_max_spread=0.20,
        btc_tick_min_depth_multiple=0.0,
        btc_scalp_min_entry_seconds=1,
    )


def test_tick_engine_does_not_require_momentum_warmup(monkeypatch):
    feed = FakeFeed(price=100_050.0, m15=None, m30=None)
    end = feed.now + timedelta(minutes=5)
    monkeypatch.setattr(
        btc_tick,
        "fetch_both_books",
        lambda market: (
            BookSnapshot(0.55, 0.56, 500, 500),
            BookSnapshot(0.43, 0.44, 500, 500),
        ),
    )
    monkeypatch.setattr(btc_tick, "current_paper_equity", lambda s=SETTINGS: 10_000.0)
    signal = BtcTickEngine(_settings(), feed=feed).evaluate(
        _market(end), feed.now - timedelta(minutes=10), end
    )
    assert signal.action in {"UP", "DOWN"}
    assert signal.momentum_15s_bps is None
    assert signal.momentum_30s_bps is None


def test_tick_engine_checks_both_sides_and_selects_best_executable_edge(monkeypatch):
    feed = FakeFeed(price=100_050.0, m15=1.0, m30=1.5)
    end = feed.now + timedelta(minutes=5)
    monkeypatch.setattr(
        btc_tick,
        "fetch_both_books",
        lambda market: (
            BookSnapshot(0.80, 0.82, 500, 500),
            BookSnapshot(0.16, 0.18, 500, 500),
        ),
    )
    monkeypatch.setattr(btc_tick, "current_paper_equity", lambda s=SETTINGS: 10_000.0)
    signal = BtcTickEngine(_settings(), feed=feed).evaluate(
        _market(end), feed.now - timedelta(minutes=10), end
    )
    # The engine ranks executable economics, rather than blindly buying UP just
    # because BTC is above the window open.
    assert signal.action in {"UP", "DOWN"}
    assert signal.selected_ask in {0.82, 0.18}


def test_tick_engine_can_use_other_side_when_one_book_is_empty(monkeypatch):
    feed = FakeFeed(price=99_980.0)
    end = feed.now + timedelta(minutes=5)
    monkeypatch.setattr(
        btc_tick,
        "fetch_both_books",
        lambda market: (None, BookSnapshot(0.50, 0.51, 500, 500)),
    )
    monkeypatch.setattr(btc_tick, "current_paper_equity", lambda s=SETTINGS: 10_000.0)
    signal = BtcTickEngine(_settings(), feed=feed).evaluate(
        _market(end), feed.now - timedelta(minutes=10), end
    )
    assert signal.action == "DOWN"
    assert signal.selected_ask == 0.51


def test_tick_engine_passes_cleanly_when_neither_side_is_executable(monkeypatch):
    feed = FakeFeed()
    end = feed.now + timedelta(minutes=5)
    monkeypatch.setattr(btc_tick, "fetch_both_books", lambda market: (None, None))
    monkeypatch.setattr(btc_tick, "current_paper_equity", lambda s=SETTINGS: 10_000.0)
    signal = BtcTickEngine(_settings(), feed=feed).evaluate(
        _market(end), feed.now - timedelta(minutes=10), end
    )
    assert signal.action == "PASS"
    assert "no executable UP/DOWN book" in signal.reason
