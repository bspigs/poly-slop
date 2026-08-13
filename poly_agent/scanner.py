from __future__ import annotations

from .config import SETTINGS, Settings
from .models import Market


def eligible(m: Market, s: Settings = SETTINGS) -> bool:
    return (
        m.active
        and not m.closed
        and m.liquidity >= s.min_liquidity
        and m.volume >= s.min_volume
        and s.min_market_price <= m.yes_price <= s.max_market_price
    )


def rank_markets(markets: list[Market], s: Settings = SETTINGS) -> list[Market]:
    """Prioritize liquid, active markets away from 0/1 extremes."""
    filtered = [m for m in markets if eligible(m, s)]

    def score(m: Market) -> float:
        uncertainty = 1 - abs(m.yes_price - 0.5) * 2
        liquidity_score = min(m.liquidity / 250_000, 1)
        volume_score = min(m.volume / 1_000_000, 1)
        return 0.45 * uncertainty + 0.35 * liquidity_score + 0.20 * volume_score

    return sorted(filtered, key=score, reverse=True)[: s.max_markets]
