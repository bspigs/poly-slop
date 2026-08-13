from __future__ import annotations

from datetime import datetime, timezone

from .config import SETTINGS, Settings
from .models import Market


def days_to_resolution(m: Market, now: datetime | None = None) -> float | None:
    if m.end_date is None:
        return None
    current = now or datetime.now(timezone.utc)
    end = m.end_date
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return (end - current).total_seconds() / 86400


def eligible(
    m: Market,
    s: Settings = SETTINGS,
    *,
    enforce_resolution_window: bool = True,
    now: datetime | None = None,
) -> bool:
    if not (
        m.active
        and not m.closed
        and m.liquidity >= s.min_liquidity
        and m.volume >= s.min_volume
        and s.min_market_price <= m.yes_price <= s.max_market_price
    ):
        return False

    if not enforce_resolution_window:
        return True

    days = days_to_resolution(m, now)
    return (
        days is not None
        and s.min_days_to_resolution <= days <= s.max_days_to_resolution
    )


def rank_markets(
    markets: list[Market],
    s: Settings = SETTINGS,
    *,
    enforce_resolution_window: bool = True,
    now: datetime | None = None,
) -> list[Market]:
    """Prioritize liquid, uncertain, near-term markets."""
    filtered = [
        m
        for m in markets
        if eligible(
            m,
            s,
            enforce_resolution_window=enforce_resolution_window,
            now=now,
        )
    ]

    def score(m: Market) -> float:
        uncertainty = 1 - abs(m.yes_price - 0.5) * 2
        liquidity_score = min(m.liquidity / 250_000, 1)
        volume_score = min(m.volume / 1_000_000, 1)
        days = days_to_resolution(m, now)
        if days is None or not enforce_resolution_window:
            urgency_score = 0.5
        else:
            window = max(s.max_days_to_resolution - s.min_days_to_resolution, 0.01)
            urgency_score = 1 - min(max(days - s.min_days_to_resolution, 0) / window, 1)
        return (
            0.35 * uncertainty
            + 0.25 * liquidity_score
            + 0.15 * volume_score
            + 0.25 * urgency_score
        )

    return sorted(filtered, key=score, reverse=True)[: s.max_markets]
