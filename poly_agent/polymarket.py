from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import requests

from .config import SETTINGS, Settings
from .models import Market

GAMMA = "https://gamma-api.polymarket.com"
BTC_15M_PREFIX = "btc-updown-15m-"
BTC_15M_SECONDS = 15 * 60


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
    token_ids = _parse_jsonish(raw.get("clobTokenIds"))
    if len(outcomes) < 2 or len(prices) < 2:
        return None

    labels = [str(o).strip() for o in outcomes]
    price_by_outcome = {label.lower(): _num(p) for label, p in zip(labels, prices)}
    label_by_key = {label.lower(): label for label in labels}
    token_by_key = {
        label.lower(): str(token)
        for label, token in zip(labels, token_ids)
        if token is not None
    }

    if "yes" in price_by_outcome and "no" in price_by_outcome:
        positive_key, negative_key = "yes", "no"
    elif "up" in price_by_outcome and "down" in price_by_outcome:
        positive_key, negative_key = "up", "down"
    else:
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
        yes_price=price_by_outcome[positive_key],
        no_price=price_by_outcome[negative_key],
        positive_label=label_by_key[positive_key].upper(),
        negative_label=label_by_key[negative_key].upper(),
        positive_token_id=token_by_key.get(positive_key),
        negative_token_id=token_by_key.get(negative_key),
        liquidity=_num(raw.get("liquidityNum", raw.get("liquidity"))),
        volume=_num(raw.get("volumeNum", raw.get("volume"))),
        active=bool(raw.get("active", True)),
        closed=bool(raw.get("closed", False)),
        description=str(raw.get("description") or ""),
    )


def fetch_market_by_id(market_id: str) -> Market:
    response = requests.get(f"{GAMMA}/markets/{market_id}", timeout=20)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected market payload for {market_id}")
    market = normalize_market(payload)
    if market is None:
        raise ValueError(f"Could not normalize market {market_id}")
    return market


def fetch_market_by_slug(slug: str) -> Market:
    response = requests.get(f"{GAMMA}/markets/slug/{slug}", timeout=20)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError(f"Unexpected market payload for slug {slug}")
    market = normalize_market(payload)
    if market is None:
        raise ValueError(f"Could not normalize market slug {slug}")
    return market


def btc_15m_slug(now: datetime | None = None) -> tuple[str, datetime, datetime]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    epoch = int(current.timestamp())
    start_epoch = (epoch // BTC_15M_SECONDS) * BTC_15M_SECONDS
    start = datetime.fromtimestamp(start_epoch, timezone.utc)
    end = start + timedelta(seconds=BTC_15M_SECONDS)
    return f"{BTC_15M_PREFIX}{start_epoch}", start, end


def fetch_current_btc_15m_market(now: datetime | None = None) -> Market:
    slug, _, end = btc_15m_slug(now)
    try:
        market = fetch_market_by_slug(slug)
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            raise RuntimeError(
                f"Current BTC 15m market '{slug}' is not published yet. Retry in a few seconds."
            ) from exc
        raise

    if not market.positive_token_id or not market.negative_token_id:
        raise RuntimeError("BTC 15m market is missing CLOB token IDs.")
    return market.model_copy(update={"end_date": end})


def fetch_markets(
    limit: int = 100,
    s: Settings = SETTINGS,
    now: datetime | None = None,
) -> list[Market]:
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
