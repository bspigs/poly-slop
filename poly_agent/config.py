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

    # BTC 15-minute scalp paper simulator. P&L thresholds are NET of estimated
    # taker fees. The stop has a short grace period so normal entry noise does
    # not instantly kill a position.
    btc_scalp_poll_seconds: float = _f("BTC_SCALP_POLL_SECONDS", 2.0)
    btc_scalp_take_profit_usd: float = _f("BTC_SCALP_TAKE_PROFIT_USD", 0.05)
    btc_scalp_trail_arm_usd: float = _f("BTC_SCALP_TRAIL_ARM_USD", 0.04)
    btc_scalp_trail_giveback_usd: float = _f("BTC_SCALP_TRAIL_GIVEBACK_USD", 0.02)
    btc_scalp_stop_loss_usd: float = _f("BTC_SCALP_STOP_LOSS_USD", 0.75)
    btc_scalp_stop_grace_seconds: int = _i("BTC_SCALP_STOP_GRACE_SECONDS", 45)
    btc_scalp_force_exit_seconds: int = _i("BTC_SCALP_FORCE_EXIT_SECONDS", 20)
    btc_scalp_min_entry_seconds: int = _i("BTC_SCALP_MIN_ENTRY_SECONDS", 30)
    btc_clob_wait_seconds: int = _i("BTC_CLOB_WAIT_SECONDS", 20)
    btc_crypto_taker_fee_rate: float = _f("BTC_CRYPTO_TAKER_FEE_RATE", 0.07)


SETTINGS = Settings()
