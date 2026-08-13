from __future__ import annotations

from datetime import datetime, timezone

import requests

from .btc_signal import COINBASE, CoinbaseSpotFeed


class RobustCoinbaseSpotFeed(CoinbaseSpotFeed):
    """Coinbase feed with a tolerant 15-minute window-open lookup.

    Coinbase documents that historic candle data may be incomplete. The old
    implementation requested exactly one 60-second bucket and treated an empty
    response as fatal. This implementation asks for the recent candle set,
    chooses the exact window-open bucket when available, and only uses a live
    sample fallback when that sample is genuinely close to the window open.
    """

    def window_open_price(self, start: datetime) -> float:
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        start = start.astimezone(timezone.utc)
        key = int(start.timestamp())

        if key in self._open_cache:
            return self._open_cache[key]

        # Coinbase's Exchange candles endpoint returns a recent range when
        # start/end are omitted. This is much more reliable than asking for one
        # exact, possibly still-forming one-minute bucket.
        response = requests.get(
            f"{COINBASE}/products/BTC-USD/candles",
            params={"granularity": 60},
            headers={"Cache-Control": "no-cache"},
            timeout=10,
        )
        response.raise_for_status()
        rows = response.json()
        parsed = [
            row
            for row in rows
            if isinstance(row, list) and len(row) >= 5
        ] if isinstance(rows, list) else []

        exact = next((row for row in parsed if int(row[0]) == key), None)
        if exact is not None:
            price = float(exact[3])
            self._open_cache[key] = price
            return price

        # A second, padded request helps when Coinbase's default recent range is
        # lagging or ordered unexpectedly. We still require the exact bucket.
        now = datetime.now(timezone.utc)
        padded_end = max(now, start)
        padded_start = start.replace(second=0, microsecond=0)
        padded_start = padded_start.fromtimestamp(key - 5 * 60, timezone.utc)
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
        rows = response.json()
        parsed = [
            row
            for row in rows
            if isinstance(row, list) and len(row) >= 5
        ] if isinstance(rows, list) else []
        exact = next((row for row in parsed if int(row[0]) == key), None)
        if exact is not None:
            price = float(exact[3])
            self._open_cache[key] = price
            return price

        # If the process was already alive right around the boundary, its first
        # live ticker sample is a safe proxy. Do not use a sample captured far
        # into the window because that would bias the signal.
        near_open = [
            sample
            for sample in self.samples
            if 0 <= (sample.ts - start).total_seconds() <= 15
        ]
        if near_open:
            price = near_open[0].price
            self._open_cache[key] = price
            return price

        # This is temporary, not fatal. The v2 loop will wait and try again.
        raise RuntimeError(
            "BTC window-open candle is not available from Coinbase yet; waiting for it to publish."
        )
