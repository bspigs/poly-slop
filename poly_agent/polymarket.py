from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .config import SETTINGS, Settings
from .models import Market

GAMMA = "https://gamma-api.polymarket.com"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _parse_jsonish(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def normalize_market(raw: dict[str, Any]) -> Market | None:
    outcomes = _parse_jsonish(raw.get("outcomes"))
    prices = _parse_jsonish(raw.get("outcomePrices"))
    if len(outcomes) < 2 or len(prices) < 2:
        return None

    price_by_outcome = {str(o).strip().lower(): _num(p) for o, p in zip(outcomes, prices)}
    if "yes" not in price_by_outcome or "no" not in price_by_outcome:
        return None

    question = str(raw.get("question") or "").strip()
    market_id = str(raw.get("id") or raw.get("conditionId") or "").strip()
    if not question or not market_id:
        return None

    return Market(
        id=market_id,
        question=question,
        slug=raw.get("slug"),
        end_date=_parse_dt(raw.get("endDate") or raw.get("end_date_iso")),
        yes_price=price_by_outcome["yes"],
        no_price=price_by_outcome["no"],
        liquidity=_num(raw.get("liquidityNum", raw.get("liquidity"))),
        volume=_num(raw.get("volumeNum", raw.get("volume"))),
        active=bool(raw.get("active", True)),
        closed=bool(raw.get("closed", False)),
        description=str(raw.get("description") or ""),
    )


def fetch_markets(
    limit: int = 100,
    s: Settings = SETTINGS,
    now: datetime | None = None,
) -> list[Market]:
    """Fetch active Polymarket markets inside the configured resolution window."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    start = current + timedelta(days=s.min_days_to_resolution)
    end = current + timedelta(days=s.max_days_to_resolution)

    response = requests.get(
        f"{GAMMA}/markets",
        params={
            "active": "true",
            "closed": "false",
            "limit": limit,
            "end_date_min": start.isoformat(),
            "end_date_max": end.isoformat(),
            "liquidity_num_min": s.min_liquidity,
            "volume_num_min": s.min_volume,
        },
        timeout=20,
    )
    response.raise_for_status()
    payload = response.json()
    rows = payload if isinstance(payload, list) else payload.get("data", [])

    markets: list[Market] = []
    for raw in rows:
        if isinstance(raw, dict):
            market = normalize_market(raw)
            if market:
                markets.append(market)
    return markets


def load_markets_from_file(path: str) -> list[Market]:
    with open(path, "r", encoding="utf-8") as f:
        rows = json.load(f)
    markets: list[Market] = []
    for raw in rows:
        if isinstance(raw, dict):
            market = normalize_market(raw)
            if market:
                markets.append(market)
    return markets
