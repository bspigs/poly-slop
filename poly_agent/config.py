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
    research_provider: str = os.getenv("RESEARCH_PROVIDER", "auto").lower()
    openai_api_key: str | None = os.getenv("OPENAI_API_KEY") or None
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-5.6")
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "qwen3:8b")
    research_timeout: int = _i("RESEARCH_TIMEOUT", 180)
    max_markets: int = _i("MAX_MARKETS", 30)
    min_liquidity: float = _f("MIN_LIQUIDITY", 25_000)
    min_volume: float = _f("MIN_VOLUME", 10_000)
    min_market_price: float = _f("MIN_MARKET_PRICE", 0.08)
    max_market_price: float = _f("MAX_MARKET_PRICE", 0.92)
    min_days_to_resolution: float = _f("MIN_DAYS_TO_RESOLUTION", 0.0)
    max_days_to_resolution: float = _f("MAX_DAYS_TO_RESOLUTION", 7.0)
    min_edge: float = _f("MIN_EDGE", 0.08)
    min_confidence: float = _f("MIN_CONFIDENCE", 0.60)
    max_position_pct: float = _f("MAX_POSITION_PCT", 0.01)
    baseline_position_pct: float = _f("BASELINE_POSITION_PCT", 0.0025)
    starting_bankroll: float = _f("STARTING_BANKROLL", 10_000)


SETTINGS = Settings()
