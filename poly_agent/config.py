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

    # BTC 15-minute paper scalp execution settings. P&L thresholds are NET of
    # estimated taker fees and use executable CLOB ask-entry / bid-exit prices.
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

    # BTC v2 microstructure / momentum signal. The signal estimates the chance
    # of finishing above the window-open BTC/USD price from distance-to-open and
    # realized volatility, then requires short-horizon momentum and executable
    # Polymarket economics to agree before a paper entry is allowed.
    btc_signal_warmup_seconds: int = _i("BTC_SIGNAL_WARMUP_SECONDS", 35)
    btc_signal_poll_seconds: float = _f("BTC_SIGNAL_POLL_SECONDS", 1.0)
    btc_signal_reentry_cooldown_seconds: float = _f("BTC_SIGNAL_REENTRY_COOLDOWN_SECONDS", 5.0)
    btc_signal_min_momentum_bps: float = _f("BTC_SIGNAL_MIN_MOMENTUM_BPS", 2.0)
    btc_signal_momentum_scale_bps: float = _f("BTC_SIGNAL_MOMENTUM_SCALE_BPS", 5.0)
    btc_signal_max_momentum_probability_boost: float = _f(
        "BTC_SIGNAL_MAX_MOMENTUM_PROBABILITY_BOOST", 0.04
    )
    btc_signal_max_adverse_accel_bps: float = _f("BTC_SIGNAL_MAX_ADVERSE_ACCEL_BPS", 4.0)
    btc_signal_min_fair_probability: float = _f("BTC_SIGNAL_MIN_FAIR_PROBABILITY", 0.58)
    btc_signal_min_fee_adjusted_edge: float = _f("BTC_SIGNAL_MIN_FEE_ADJUSTED_EDGE", 0.025)
    btc_signal_max_spread: float = _f("BTC_SIGNAL_MAX_SPREAD", 0.03)
    btc_signal_min_contract_price: float = _f("BTC_SIGNAL_MIN_CONTRACT_PRICE", 0.15)
    btc_signal_max_contract_price: float = _f("BTC_SIGNAL_MAX_CONTRACT_PRICE", 0.85)
    btc_signal_min_depth_multiple: float = _f("BTC_SIGNAL_MIN_DEPTH_MULTIPLE", 1.25)
    btc_signal_reversal_check_after_seconds: float = _f(
        "BTC_SIGNAL_REVERSAL_CHECK_AFTER_SECONDS", 15.0
    )
    btc_signal_reversal_min_confidence: float = _f(
        "BTC_SIGNAL_REVERSAL_MIN_CONFIDENCE", 0.62
    )


SETTINGS = Settings()
