from dataclasses import replace
from datetime import datetime, timedelta, timezone

from poly_agent import btc_signal
from poly_agent.btc_signal import BookSnapshot, BtcSignalEngine, SpotSample, normal_cdf
from poly_agent.config import SETTINGS
from poly_agent.models import Market


class FakeFeed:
    def __init__(self, *, price=100_050.0, m15=3.0, m30=5.0):
        self.now = datetime.now(timezone.utc)
        self._price = price
        self._m15 = m15
        self._m30 = m30

    def sample(self):
        return SpotSample(self.now, self._price, self._price - 1, self._price + 1)

    def warm_seconds(self):
        return 60.0

    def window_open_price(self, start):
        return 100_000.0

    def sigma_per_sqrt_second(self):
        return 0.000025

    def momentum_bps(self, seconds):
        if seconds == 15:
            return self._m15
        if seconds == 30:
            return self._m30
        return None


def _market(end):
    return Market(
        id="btc-v2-test",
        question="Bitcoin Up or Down - test",
        end_date=end,
        yes_price=0.60,
        no_price=0.40,
        positive_label="UP",
        negative_label="DOWN",
        positive_token_id="up-token",
        negative_token_id="down-token",
    )


def test_normal_cdf_center():
    assert normal_cdf(0.0) == 0.5


def test_v2_passes_when_short_momentum_is_too_weak(monkeypatch):
    feed = FakeFeed(m15=0.2, m30=0.4)
    end = feed.now + timedelta(minutes=5)
    engine = BtcSignalEngine(SETTINGS, feed=feed)
    monkeypatch.setattr(
        btc_signal,
        "fetch_book_snapshot",
        lambda token_id: BookSnapshot(0.59, 0.60, 500, 500),
    )
    monkeypatch.setattr(btc_signal, "current_paper_equity", lambda s=SETTINGS: 10_000.0)
    signal = engine.evaluate(_market(end), feed.now - timedelta(minutes=10), end)
    assert signal.action == "PASS"
    assert "momentum too weak" in signal.reason


def test_v2_enters_when_probability_momentum_and_execution_all_clear(monkeypatch):
    feed = FakeFeed(m15=3.0, m30=5.0)
    end = feed.now + timedelta(minutes=5)
    settings = replace(
        SETTINGS,
        btc_signal_min_momentum_bps=1.0,
        btc_signal_min_fee_adjusted_edge=0.02,
        btc_signal_min_fair_probability=0.57,
    )
    engine = BtcSignalEngine(settings, feed=feed)
    monkeypatch.setattr(
        btc_signal,
        "fetch_book_snapshot",
        lambda token_id: BookSnapshot(0.59, 0.60, 500, 500),
    )
    monkeypatch.setattr(btc_signal, "current_paper_equity", lambda s=settings: 10_000.0)
    signal = engine.evaluate(_market(end), feed.now - timedelta(minutes=10), end)
    assert signal.action == "UP"
    assert signal.side == "YES"
    assert signal.fee_adjusted_edge > settings.btc_signal_min_fee_adjusted_edge


def test_v2_rejects_wide_polymarket_spread(monkeypatch):
    feed = FakeFeed(m15=3.0, m30=5.0)
    end = feed.now + timedelta(minutes=5)
    engine = BtcSignalEngine(SETTINGS, feed=feed)
    monkeypatch.setattr(
        btc_signal,
        "fetch_book_snapshot",
        lambda token_id: BookSnapshot(0.50, 0.60, 500, 500),
    )
    monkeypatch.setattr(btc_signal, "current_paper_equity", lambda s=SETTINGS: 10_000.0)
    signal = engine.evaluate(_market(end), feed.now - timedelta(minutes=10), end)
    assert signal.action == "PASS"
    assert "spread too wide" in signal.reason
