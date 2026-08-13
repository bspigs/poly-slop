from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _f(name: str, default: float) -> float:
    return float(os.getenv(name, default))


def _i(name: str, default: int) -> int:
    return int(os.getenv(name, default))


@dataclass(frozen=True)
class Settings:
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY") or None
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.6")
    max_markets: int = _i("MAX_MARKETS", 30)
    min_liquidity: float = _f("MIN_LIQUIDITY", 25_000)
    min_volume: float = _f("MIN_VOLUME", 10_000)
    min_market_price: float = _f("MIN_MARKET_PRICE", 0.08)
    max_market_price: float = _f("MAX_MARKET_PRICE", 0.92)
    min_edge: float = _f("MIN_EDGE", 0.08)
    min_confidence: float = _f("MIN_CONFIDENCE", 0.60)
    max_position_pct: float = _f("MAX_POSITION_PCT", 0.01)
    starting_bankroll: float = _f("STARTING_BANKROLL", 10_000)


SETTINGS = Settings()
