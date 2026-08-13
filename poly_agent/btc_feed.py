from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from .btc_signal import COINBASE, CoinbaseSpotFeed


class WindowOpenUnavailable(RuntimeError):
    """Raised when Coinbase has not published the exact window-open candle yet."""


class RobustCoinbaseSpotFeed(CoinbaseSpotFeed):
    """Coinbase feed with a tolerant 15-minute window-open lookup.

    Coinbase documents that historic candle data may be incomplete. The old
    implementation requested exactly one 60-second bucket and treated an empty
    response as fatal. This implementation asks for a broad recent candle set,
    then a padded range around the target minute, and only uses a live sample
    fallback when that sample was captured very close to the window boundary.
    """

    @staticmethod
    def _parsed_rows(rows: object) -> list[list[object]]:
        if not isinstance(rows, list):
            return []
        return [row for row in rows if isinstance(row, list) and len(row) >= 5]

    @staticmethod
    def _exact_open(rows: list[list[object]], key: int) -> float | None:
        row = next((row for row in rows if int(row[0]) == key), None)
        return None if row is None else float(row[3])

    def window_open_price(self, start: datetime) -> float:
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        start = start.astimezone(timezone.utc)
        key = int(start.timestamp())

        if key in self._open_cache:
            return self._open_cache[key]

        # First try Coinbase's default recent history. With start/end omitted,
        # Coinbase selects a range ending now, which is more tolerant than a
        # one-bucket request for a candle that may still be forming.
        response = requests.get(
            f"{COINBASE}/products/BTC-USD/candles",
            params={"granularity": 60},
            headers={"Cache-Control": "no-cache"},
            timeout=10,
        )
        response.raise_for_status()
        price = self._exact_open(self._parsed_rows(response.json()), key)
        if price is not None:
            self._open_cache[key] = price
            return price

        # Then ask for a padded range around the target minute. Coinbase may
        # include buckets before the declared start, so we still select by exact
        # timestamp rather than trusting response order.
        now = datetime.now(timezone.utc)
        padded_start = start - timedelta(minutes=5)
        padded_end = max(now, start + timedelta(minutes=1))
        response = requests.get(
            f"{COINBASE}/products/BTC-USD/candles",
            params={
                "granularity": 60,
                "start": padded_start.isoformat(),
                "end": padded_end.isoformat(),
            },
            headers={"Cache-Control": "no-cache"},
            timeout=10,
        )
        response.raise_for_status()
        price = self._exact_open(self._parsed_rows(response.json()), key)
        if price is not None:
            self._open_cache[key] = price
            return price

        # If this process was already sampling right around the boundary, use
        # that observation as a close proxy. Never substitute a sample captured
        # deep into the window because that would bias P(up/down).
        near_open = [
            sample
            for sample in self.samples
            if 0 <= (sample.ts - start).total_seconds() <= 15
        ]
        if near_open:
            price = near_open[0].price
            self._open_cache[key] = price
            return price

        raise WindowOpenUnavailable(
            "Coinbase has not published the exact BTC window-open candle yet."
        )
