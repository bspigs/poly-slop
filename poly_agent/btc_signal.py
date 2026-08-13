from __future__ import annotations

import math
import statistics
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

import requests
from rich.console import Console

from .btc_scalper import (
    EmptyOrderBook,
    ScalpTrade,
    close_scalp,
    current_paper_equity,
    load_scalps,
    net_exit_pnl,
    taker_fee,
    wait_for_top_of_book,
)
from .config import SETTINGS, Settings
from .models import Market

COINBASE = "https://api.exchange.coinbase.com"
CLOB = "https://clob.polymarket.com"


@dataclass(frozen=True)
class SpotSample:
    ts: datetime
    price: float
    bid: float
    ask: float


@dataclass(frozen=True)
class BookSnapshot:
    bid: float
    ask: float
    bid_size: float
    ask_size: float

    @property
    def spread(self) -> float:
        return self.ask - self.bid

    @property
    def imbalance(self) -> float:
        total = self.bid_size + self.ask_size
        return 0.0 if total <= 0 else (self.bid_size - self.ask_size) / total


@dataclass(frozen=True)
class BtcSignal:
    action: Literal["UP", "DOWN", "PASS"]
    side: Literal["YES", "NO", "PASS"]
    fair_up_probability: float
    fair_side_probability: float
    confidence: float
    fee_adjusted_edge: float
    spot_price: float
    window_open_price: float
    seconds_left: float
    distance_bps: float
    momentum_15s_bps: float | None
    momentum_30s_bps: float | None
    acceleration_bps: float | None
    sigma_15m_bps: float
    selected_bid: float | None
    selected_ask: float | None
    selected_spread: float | None
    book_imbalance: float | None
    reason: str


class CoinbaseSpotFeed:
    """Small keyless BTC/USD feed for the paper signal engine."""

    def __init__(self) -> None:
        self.samples: deque[SpotSample] = deque(maxlen=1800)
        self._open_cache: dict[int, float] = {}
        self._hist_sigma_cache: tuple[float, float] | None = None

    def sample(self) -> SpotSample:
        response = requests.get(
            f"{COINBASE}/products/BTC-USD/ticker",
            headers={"Cache-Control": "no-cache"},
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        ts_raw = payload.get("time")
        try:
            ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
        except Exception:
            ts = datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        sample = SpotSample(
            ts=ts,
            price=float(payload["price"]),
            bid=float(payload["bid"]),
            ask=float(payload["ask"]),
        )
        if not self.samples or sample.ts > self.samples[-1].ts:
            self.samples.append(sample)
        else:
            self.samples.append(
                SpotSample(datetime.now(timezone.utc), sample.price, sample.bid, sample.ask)
            )
        return self.samples[-1]

    def warm_seconds(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        return max((self.samples[-1].ts - self.samples[0].ts).total_seconds(), 0.0)

    def _sample_at_age(self, seconds: float) -> SpotSample | None:
        if not self.samples:
            return None
        target = self.samples[-1].ts - timedelta(seconds=seconds)
        candidates = [s for s in self.samples if s.ts <= target]
        return candidates[-1] if candidates else None

    def momentum_bps(self, seconds: float) -> float | None:
        if not self.samples:
            return None
        old = self._sample_at_age(seconds)
        if old is None or old.price <= 0:
            return None
        return math.log(self.samples[-1].price / old.price) * 10_000

    def window_open_price(self, start: datetime) -> float:
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        key = int(start.timestamp())
        if key in self._open_cache:
            return self._open_cache[key]

        response = requests.get(
            f"{COINBASE}/products/BTC-USD/candles",
            params={
                "granularity": 60,
                "start": start.isoformat(),
                "end": (start + timedelta(seconds=60)).isoformat(),
            },
            timeout=10,
        )
        response.raise_for_status()
        rows = response.json()
        if not isinstance(rows, list) or not rows:
            raise RuntimeError("Coinbase returned no BTC candle for the 15-minute window open.")

        parsed = [row for row in rows if isinstance(row, list) and len(row) >= 5]
        exact = next((row for row in parsed if int(row[0]) == key), None)
        row = exact or min(parsed, key=lambda r: abs(int(r[0]) - key))
        price = float(row[3])
        self._open_cache[key] = price
        return price

    def historical_sigma_per_sqrt_second(self) -> float:
        now_mono = time.monotonic()
        if self._hist_sigma_cache and now_mono - self._hist_sigma_cache[0] < 30:
            return self._hist_sigma_cache[1]

        response = requests.get(
            f"{COINBASE}/products/BTC-USD/candles",
            params={"granularity": 60},
            timeout=10,
        )
        response.raise_for_status()
        rows = response.json()
        parsed = sorted(
            [row for row in rows if isinstance(row, list) and len(row) >= 5],
            key=lambda r: int(r[0]),
        )[-31:]
        closes = [float(row[4]) for row in parsed if float(row[4]) > 0]
        if len(closes) < 5:
            sigma = 0.00008
        else:
            returns = [math.log(b / a) for a, b in zip(closes, closes[1:]) if a > 0 and b > 0]
            sigma_60 = statistics.pstdev(returns) if len(returns) >= 2 else abs(returns[0])
            sigma = sigma_60 / math.sqrt(60)
        sigma = max(sigma, 0.000025)
        self._hist_sigma_cache = (now_mono, sigma)
        return sigma

    def local_sigma_per_sqrt_second(self) -> float | None:
        recent = [s for s in self.samples if (self.samples[-1].ts - s.ts).total_seconds() <= 120] if self.samples else []
        normalized: list[float] = []
        for a, b in zip(recent, recent[1:]):
            dt = (b.ts - a.ts).total_seconds()
            if dt <= 0 or a.price <= 0 or b.price <= 0:
                continue
            normalized.append(math.log(b.price / a.price) / math.sqrt(dt))
        if len(normalized) < 8:
            return None
        return math.sqrt(sum(x * x for x in normalized) / len(normalized))

    def sigma_per_sqrt_second(self) -> float:
        hist = self.historical_sigma_per_sqrt_second()
        local = self.local_sigma_per_sqrt_second()
        if local is None:
            return hist
        return max(0.65 * hist + 0.35 * local, 0.000025)


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def fetch_book_snapshot(token_id: str) -> BookSnapshot:
    response = requests.get(f"{CLOB}/book", params={"token_id": token_id}, timeout=10)
    response.raise_for_status()
    payload = response.json()
    bids = payload.get("bids") or []
    asks = payload.get("asks") or []
    if not bids or not asks:
        raise EmptyOrderBook("CLOB order book has no executable bid/ask yet.")
    best_bid_row = max(bids, key=lambda row: float(row["price"]))
    best_ask_row = min(asks, key=lambda row: float(row["price"]))
    return BookSnapshot(
        bid=float(best_bid_row["price"]),
        ask=float(best_ask_row["price"]),
        bid_size=float(best_bid_row.get("size", 0.0)),
        ask_size=float(best_ask_row.get("size", 0.0)),
    )


class BtcSignalEngine:
    def __init__(self, s: Settings = SETTINGS, feed: CoinbaseSpotFeed | None = None) -> None:
        self.s = s
        self.feed = feed or CoinbaseSpotFeed()

    def evaluate(
        self,
        market: Market,
        window_start: datetime,
        window_end: datetime,
    ) -> BtcSignal:
        latest = self.feed.sample()
        now = latest.ts
        if window_start.tzinfo is None:
            window_start = window_start.replace(tzinfo=timezone.utc)
        if window_end.tzinfo is None:
            window_end = window_end.replace(tzinfo=timezone.utc)
        seconds_left = max((window_end - now).total_seconds(), 0.0)
        open_price = self.feed.window_open_price(window_start)
        distance_bps = math.log(latest.price / open_price) * 10_000
        sigma_sec = self.feed.sigma_per_sqrt_second()
        sigma_remaining = max(sigma_sec * math.sqrt(max(seconds_left, 1.0)), 1e-9)
        sigma_15m_bps = sigma_sec * math.sqrt(900) * 10_000
        base_z = math.log(latest.price / open_price) / sigma_remaining
        base_p_up = min(max(normal_cdf(base_z), 0.01), 0.99)

        m15 = self.feed.momentum_bps(15)
        m30 = self.feed.momentum_bps(30)
        acceleration = None if m15 is None or m30 is None else 2 * m15 - m30

        if self.feed.warm_seconds() < self.s.btc_signal_warmup_seconds or m15 is None or m30 is None:
            return BtcSignal(
                action="PASS",
                side="PASS",
                fair_up_probability=base_p_up,
                fair_side_probability=max(base_p_up, 1 - base_p_up),
                confidence=0.0,
                fee_adjusted_edge=0.0,
                spot_price=latest.price,
                window_open_price=open_price,
                seconds_left=seconds_left,
                distance_bps=distance_bps,
                momentum_15s_bps=m15,
                momentum_30s_bps=m30,
                acceleration_bps=acceleration,
                sigma_15m_bps=sigma_15m_bps,
                selected_bid=None,
                selected_ask=None,
                selected_spread=None,
                book_imbalance=None,
                reason=f"Signal warming up ({self.feed.warm_seconds():.0f}s/{self.s.btc_signal_warmup_seconds}s).",
            )

        normalized_momentum = 0.65 * m15 + 0.35 * (m30 / 2.0)
        momentum_adjustment = self.s.btc_signal_max_momentum_probability_boost * math.tanh(
            normalized_momentum / max(self.s.btc_signal_momentum_scale_bps, 0.1)
        )
        accel_adjustment = 0.01 * math.tanh((acceleration or 0.0) / 4.0)
        fair_up = min(max(base_p_up + momentum_adjustment + accel_adjustment, 0.01), 0.99)

        direction_up = fair_up >= 0.5
        action: Literal["UP", "DOWN"] = "UP" if direction_up else "DOWN"
        side: Literal["YES", "NO"] = "YES" if direction_up else "NO"
        token_id = market.positive_token_id if direction_up else market.negative_token_id
        if not token_id:
            raise RuntimeError(f"BTC market is missing token ID for {action}.")
        book = fetch_book_snapshot(token_id)
        fair_side = fair_up if direction_up else 1 - fair_up
        fee_cushion_per_share = 2 * self.s.btc_crypto_taker_fee_rate * book.ask * (1 - book.ask)
        fee_adjusted_edge = fair_side - book.ask - fee_cushion_per_share
        aligned_momentum = normalized_momentum if direction_up else -normalized_momentum
        aligned_accel = (acceleration or 0.0) if direction_up else -(acceleration or 0.0)
        confidence = min(
            0.97,
            0.50 + 0.9 * abs(fair_up - 0.5) + min(max(aligned_momentum, 0.0) / 40.0, 0.12),
        )

        reasons: list[str] = []
        if seconds_left < self.s.btc_scalp_min_entry_seconds:
            reasons.append("entry cutoff")
        if abs(normalized_momentum) < self.s.btc_signal_min_momentum_bps:
            reasons.append("momentum too weak")
        if aligned_momentum <= 0:
            reasons.append("momentum disagrees")
        if aligned_accel < -self.s.btc_signal_max_adverse_accel_bps:
            reasons.append("momentum decelerating")
        if fair_side < self.s.btc_signal_min_fair_probability:
            reasons.append("fair probability too low")
        if fee_adjusted_edge < self.s.btc_signal_min_fee_adjusted_edge:
            reasons.append("edge does not clear spread/fee hurdle")
        if book.spread > self.s.btc_signal_max_spread:
            reasons.append("Polymarket spread too wide")
        if not self.s.btc_signal_min_contract_price <= book.ask <= self.s.btc_signal_max_contract_price:
            reasons.append("contract price outside scalp range")

        equity = current_paper_equity(self.s)
        stake = equity * min(self.s.baseline_position_pct, self.s.max_position_pct)
        needed_shares = stake / book.ask if book.ask > 0 else float("inf")
        if book.ask_size < needed_shares * self.s.btc_signal_min_depth_multiple:
            reasons.append("insufficient top-of-book ask depth")

        if reasons:
            return BtcSignal(
                action="PASS",
                side="PASS",
                fair_up_probability=fair_up,
                fair_side_probability=fair_side,
                confidence=confidence,
                fee_adjusted_edge=fee_adjusted_edge,
                spot_price=latest.price,
                window_open_price=open_price,
                seconds_left=seconds_left,
                distance_bps=distance_bps,
                momentum_15s_bps=m15,
                momentum_30s_bps=m30,
                acceleration_bps=acceleration,
                sigma_15m_bps=sigma_15m_bps,
                selected_bid=book.bid,
                selected_ask=book.ask,
                selected_spread=book.spread,
                book_imbalance=book.imbalance,
                reason="; ".join(reasons),
            )

        return BtcSignal(
            action=action,
            side=side,
            fair_up_probability=fair_up,
            fair_side_probability=fair_side,
            confidence=confidence,
            fee_adjusted_edge=fee_adjusted_edge,
            spot_price=latest.price,
            window_open_price=open_price,
            seconds_left=seconds_left,
            distance_bps=distance_bps,
            momentum_15s_bps=m15,
            momentum_30s_bps=m30,
            acceleration_bps=acceleration,
            sigma_15m_bps=sigma_15m_bps,
            selected_bid=book.bid,
            selected_ask=book.ask,
            selected_spread=book.spread,
            book_imbalance=book.imbalance,
            reason="signal clears probability, momentum, spread, fee, and depth gates",
        )


def open_signal_scalp(
    market: Market,
    signal: BtcSignal,
    *,
    s: Settings = SETTINGS,
    console: Console | None = None,
) -> ScalpTrade:
    console = console or Console()
    if signal.side == "PASS":
        raise RuntimeError("Cannot open a scalp from a PASS signal.")
    token_id = market.positive_token_id if signal.side == "YES" else market.negative_token_id
    side_label = market.positive_label if signal.side == "YES" else market.negative_label
    if not token_id:
        raise RuntimeError(f"No token ID for {side_label}.")

    equity = current_paper_equity(s)
    stake = round(equity * min(s.baseline_position_pct, s.max_position_pct), 2)
    best_bid, best_ask = wait_for_top_of_book(
        token_id,
        max_wait_seconds=float(s.btc_clob_wait_seconds),
        console=console,
    )
    shares = stake / best_ask
    entry_fee = taker_fee(shares, best_ask, s)
    initial_exit_fee = taker_fee(shares, best_bid, s)
    initial_net = shares * (best_bid - best_ask) - entry_fee - initial_exit_fee
    end = market.end_date or (datetime.now(timezone.utc) + timedelta(minutes=15))
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    trade = ScalpTrade(
        market_id=market.id,
        question=market.question,
        side=signal.side,
        side_label=side_label,
        token_id=token_id,
        entry_time=datetime.now(timezone.utc),
        window_end=end,
        entry_price=best_ask,
        stake=stake,
        shares=shares,
        entry_fee=entry_fee,
        model_fair_probability=signal.fair_side_probability,
        model_confidence=signal.confidence,
        peak_exit_bid=best_bid,
        peak_net_pnl=initial_net,
        strategy_version="v2-microstructure",
        signal_spot_price=signal.spot_price,
        signal_window_open_price=signal.window_open_price,
        signal_distance_bps=signal.distance_bps,
        signal_momentum_15s_bps=signal.momentum_15s_bps,
        signal_momentum_30s_bps=signal.momentum_30s_bps,
        signal_fee_adjusted_edge=signal.fee_adjusted_edge,
    )
    from .btc_scalper import _upsert_trade

    _upsert_trade(trade)
    console.print(
        f"[green]V2 PAPER SCALP OPENED:[/green] {side_label} ${stake:.2f} | "
        f"ask {best_ask:.3f} | fair {signal.fair_side_probability:.1%} | "
        f"fee-adj edge {signal.fee_adjusted_edge:+.1%}"
    )
    return trade


def manage_signal_scalp(
    trade: ScalpTrade,
    market: Market,
    window_start: datetime,
    engine: BtcSignalEngine,
    *,
    s: Settings = SETTINGS,
    console: Console | None = None,
) -> ScalpTrade:
    console = console or Console()
    peak_net = trade.peak_net_pnl
    last_bid: float | None = None

    while True:
        now = datetime.now(timezone.utc)
        seconds_left = (trade.window_end - now).total_seconds()
        hold_seconds = max((now - trade.entry_time).total_seconds(), 0.0)
        try:
            bid, _ = wait_for_top_of_book(
                trade.token_id,
                max_wait_seconds=min(float(s.btc_clob_wait_seconds), max(seconds_left, 1.0)),
                console=console,
            )
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            if seconds_left <= 0:
                console.print("[yellow]No executable exit through expiry; leaving OPEN for resolution recovery.[/yellow]")
                return trade
            console.print(f"[dim]Exit book unavailable: {exc}[/dim]")
            time.sleep(max(s.btc_scalp_poll_seconds, 0.5))
            continue

        net, _ = net_exit_pnl(trade, bid, s)
        peak_net = max(peak_net, net)
        from .btc_scalper import _upsert_trade

        trade = trade.model_copy(update={"peak_exit_bid": max(trade.peak_exit_bid, bid), "peak_net_pnl": peak_net})
        _upsert_trade(trade)

        if last_bid != bid:
            console.print(
                f"[dim]{trade.side_label} bid {bid:.3f} | net ${net:+.2f} | peak ${peak_net:+.2f} | "
                f"{max(seconds_left, 0):.0f}s left[/dim]"
            )
            last_bid = bid

        reason: str | None = None
        if net >= s.btc_scalp_take_profit_usd:
            reason = "TAKE_PROFIT"
        elif hold_seconds >= s.btc_scalp_stop_grace_seconds and net <= -abs(s.btc_scalp_stop_loss_usd):
            reason = "STOP_LOSS"
        elif seconds_left <= s.btc_scalp_force_exit_seconds:
            reason = "WINDOW_TIMEOUT"
        elif hold_seconds >= s.btc_signal_reversal_check_after_seconds:
            try:
                fresh = engine.evaluate(market, window_start, trade.window_end)
                opposite = (trade.side == "YES" and fresh.action == "DOWN") or (trade.side == "NO" and fresh.action == "UP")
                if opposite and fresh.confidence >= s.btc_signal_reversal_min_confidence:
                    reason = "SIGNAL_REVERSAL"
            except EmptyOrderBook:
                pass
            except Exception as exc:
                console.print(f"[dim]Signal refresh skipped: {exc}[/dim]")

        if reason:
            closed = close_scalp(trade, bid, reason, s=s)
            equity = current_paper_equity(s)
            console.print(
                f"[bold green]V2 PAPER SCALP CLOSED:[/bold green] {closed.side_label} | "
                f"{closed.entry_price:.3f} -> {closed.exit_price:.3f} | "
                f"NET ${closed.realized_pnl:+.2f} | {reason} | equity ${equity:,.2f}"
            )
            return closed

        time.sleep(max(s.btc_scalp_poll_seconds, 0.5))


def v2_metrics(s: Settings = SETTINGS) -> dict[str, float | int]:
    trades = [t for t in load_scalps() if t.strategy_version == "v2-microstructure"]
    closed = [t for t in trades if t.status == "CLOSED" and t.realized_pnl is not None]
    wins = [t for t in closed if (t.realized_pnl or 0) > 0]
    losses = [t for t in closed if (t.realized_pnl or 0) < 0]
    pnl = sum(t.realized_pnl or 0 for t in closed)
    return {
        "trades": len(trades),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "hit_rate": len(wins) / len(closed) if closed else 0.0,
        "net_pnl": pnl,
        "paper_equity": s.starting_bankroll + pnl,
    }
