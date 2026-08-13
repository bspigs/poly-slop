from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Literal

import requests

from .btc_scalper import EmptyOrderBook, current_paper_equity
from .btc_signal import BtcSignal, BookSnapshot, CoinbaseSpotFeed, normal_cdf
from .config import SETTINGS, Settings
from .models import Market

CLOB = "https://clob.polymarket.com"


def _parse_book(payload: dict) -> BookSnapshot | None:
    bids = payload.get("bids") or []
    asks = payload.get("asks") or []
    if not bids or not asks:
        return None
    best_bid_row = max(bids, key=lambda row: float(row["price"]))
    best_ask_row = min(asks, key=lambda row: float(row["price"]))
    return BookSnapshot(
        bid=float(best_bid_row["price"]),
        ask=float(best_ask_row["price"]),
        bid_size=float(best_bid_row.get("size", 0.0)),
        ask_size=float(best_ask_row.get("size", 0.0)),
    )


def fetch_both_books(market: Market) -> tuple[BookSnapshot | None, BookSnapshot | None]:
    """Fetch UP and DOWN books in one public CLOB request.

    A token with an empty side returns None instead of aborting the whole tick.
    This lets the scanner consider the other outcome immediately.
    """
    if not market.positive_token_id or not market.negative_token_id:
        raise RuntimeError("BTC market is missing UP/DOWN CLOB token IDs.")

    token_ids = [market.positive_token_id, market.negative_token_id]
    response = requests.post(
        f"{CLOB}/books",
        json=[{"token_id": token_id} for token_id in token_ids],
        headers={"Cache-Control": "no-cache", "Content-Type": "application/json"},
        timeout=5,
    )
    response.raise_for_status()
    rows = response.json()
    if not isinstance(rows, list):
        raise RuntimeError("Unexpected Polymarket CLOB books response.")

    by_asset: dict[str, BookSnapshot | None] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        asset = str(row.get("asset_id") or "")
        if asset:
            by_asset[asset] = _parse_book(row)

    # The API normally returns asset_id. Fall back to response order if needed.
    if not by_asset:
        parsed = [_parse_book(row) if isinstance(row, dict) else None for row in rows]
        up = parsed[0] if len(parsed) > 0 else None
        down = parsed[1] if len(parsed) > 1 else None
        return up, down

    return by_asset.get(token_ids[0]), by_asset.get(token_ids[1])


class BtcTickEngine:
    """Aggressive 1 Hz paper signal engine for short-lived executable slips.

    Unlike the original v2 engine, momentum and model confidence are not hard
    entry gates. Every tick estimates fair UP probability, prices BOTH outcome
    books, and selects the side with the best executable fee-adjusted edge.
    """

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

        # Momentum improves the estimate when available, but never blocks a tick.
        normalized_momentum = 0.0
        if m15 is not None:
            normalized_momentum += 0.65 * m15
        if m30 is not None:
            normalized_momentum += 0.35 * (m30 / 2.0)
        momentum_adjustment = self.s.btc_signal_max_momentum_probability_boost * math.tanh(
            normalized_momentum / max(self.s.btc_signal_momentum_scale_bps, 0.1)
        )
        accel_adjustment = 0.0 if acceleration is None else 0.01 * math.tanh(acceleration / 4.0)
        fair_up = min(max(base_p_up + momentum_adjustment + accel_adjustment, 0.01), 0.99)

        up_book, down_book = fetch_both_books(market)
        candidates: list[tuple[float, Literal["UP", "DOWN"], Literal["YES", "NO"], float, BookSnapshot]] = []

        equity = current_paper_equity(self.s)
        stake = equity * min(self.s.baseline_position_pct, self.s.max_position_pct)

        for action, side, fair_side, book in (
            ("UP", "YES", fair_up, up_book),
            ("DOWN", "NO", 1.0 - fair_up, down_book),
        ):
            if book is None or not 0 < book.ask < 1:
                continue
            needed_shares = stake / book.ask if book.ask > 0 else float("inf")
            if book.ask_size < needed_shares * self.s.btc_tick_min_depth_multiple:
                continue
            if not self.s.btc_tick_min_contract_price <= book.ask <= self.s.btc_tick_max_contract_price:
                continue
            if book.spread > self.s.btc_tick_max_spread:
                continue

            entry_fee_per_share = self.s.btc_crypto_taker_fee_rate * book.ask * (1.0 - book.ask)
            exit_fee_per_share = self.s.btc_crypto_taker_fee_rate * book.bid * (1.0 - book.bid)
            fee_adjusted_edge = fair_side - book.ask - entry_fee_per_share - exit_fee_per_share
            candidates.append((fee_adjusted_edge, action, side, fair_side, book))

        if not candidates:
            return BtcSignal(
                action="PASS",
                side="PASS",
                fair_up_probability=fair_up,
                fair_side_probability=max(fair_up, 1.0 - fair_up),
                confidence=0.50 + 0.45 * abs(fair_up - 0.5) * 2,
                fee_adjusted_edge=-1.0,
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
                reason="no executable UP/DOWN book passes basic price/spread/depth checks",
            )

        best_edge, action, side, fair_side, book = max(candidates, key=lambda item: item[0])
        confidence = min(0.99, 0.50 + 0.9 * abs(fair_up - 0.5))

        blockers: list[str] = []
        if seconds_left < self.s.btc_scalp_min_entry_seconds:
            blockers.append("entry cutoff")
        if best_edge < self.s.btc_tick_min_fee_adjusted_edge:
            blockers.append("best side still too expensive after fees")

        if blockers:
            return BtcSignal(
                action="PASS",
                side="PASS",
                fair_up_probability=fair_up,
                fair_side_probability=fair_side,
                confidence=confidence,
                fee_adjusted_edge=best_edge,
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
                reason="; ".join(blockers),
            )

        return BtcSignal(
            action=action,
            side=side,
            fair_up_probability=fair_up,
            fair_side_probability=fair_side,
            confidence=confidence,
            fee_adjusted_edge=best_edge,
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
            reason="1 Hz slip scan selected best executable side",
        )
