from __future__ import annotations

import json
import math
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone

import websocket

RTDS_URL = "wss://ws-live-data.polymarket.com"


class ChainlinkUnavailable(RuntimeError):
    """Raised when the public Chainlink RTDS feed has not produced usable data yet."""


class ChainlinkOpenUnavailable(RuntimeError):
    """Raised when this process did not observe the current window's Chainlink open."""


@dataclass(frozen=True)
class ChainlinkSample:
    ts: datetime
    price: float


class ChainlinkBtcFeed:
    """Background public RTDS subscriber for Chainlink BTC/USD.

    The feed intentionally does not fabricate a mid-window price-to-beat. For
    directional maker trading, the process must have observed a Chainlink sample
    within a few seconds of that 15-minute window's start. This avoids mixing a
    Coinbase proxy with a market that actually resolves from Chainlink.
    """

    def __init__(self) -> None:
        self.samples: deque[ChainlinkSample] = deque(maxlen=3600)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="chainlink-btc-rtds", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()

    def _append_payload(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        symbol = str(payload.get("symbol") or "").lower()
        if symbol != "btc/usd":
            return

        # Live crypto updates use a direct value/timestamp payload. Be tolerant
        # of a possible snapshot-style data list as well.
        rows: list[tuple[int, float]] = []
        if "value" in payload:
            try:
                rows.append((int(payload.get("timestamp") or time.time() * 1000), float(payload["value"])))
            except (TypeError, ValueError):
                pass
        data = payload.get("data")
        if isinstance(data, list):
            for row in data:
                if not isinstance(row, dict):
                    continue
                try:
                    rows.append((int(row["timestamp"]), float(row["value"])))
                except (KeyError, TypeError, ValueError):
                    continue

        if not rows:
            return
        with self._lock:
            for ts_ms, price in sorted(rows):
                if price <= 0:
                    continue
                ts = datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc)
                if self.samples and ts <= self.samples[-1].ts:
                    continue
                self.samples.append(ChainlinkSample(ts=ts, price=price))

    def _run(self) -> None:
        subscription = {
            "action": "subscribe",
            "subscriptions": [
                {
                    "topic": "crypto_prices_chainlink",
                    "type": "*",
                    "filters": '{"symbol":"btc/usd"}',
                }
            ],
        }
        backoff = 1.0
        while not self._stop.is_set():
            ws = None
            try:
                ws = websocket.create_connection(RTDS_URL, timeout=10)
                ws.send(json.dumps(subscription))
                ws.settimeout(5)
                backoff = 1.0
                last_ping = time.monotonic()
                while not self._stop.is_set():
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        raw = None
                    if raw:
                        try:
                            message = json.loads(raw)
                        except (json.JSONDecodeError, TypeError):
                            message = None
                        if isinstance(message, list):
                            for item in message:
                                if isinstance(item, dict):
                                    self._append_payload(item.get("payload"))
                        elif isinstance(message, dict):
                            self._append_payload(message.get("payload"))
                    if time.monotonic() - last_ping >= 5:
                        ws.send("PING")
                        last_ping = time.monotonic()
            except Exception:
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * 2, 10.0)
            finally:
                if ws is not None:
                    try:
                        ws.close()
                    except Exception:
                        pass

    def latest(self, max_age_seconds: float = 8.0) -> ChainlinkSample:
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with self._lock:
                sample = self.samples[-1] if self.samples else None
            if sample is not None:
                age = (datetime.now(timezone.utc) - sample.ts).total_seconds()
                if age <= max_age_seconds:
                    return sample
            time.sleep(0.1)
        raise ChainlinkUnavailable("No fresh public Chainlink BTC/USD update yet.")

    def window_open_price(self, start: datetime, tolerance_seconds: float = 6.0) -> float:
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        start = start.astimezone(timezone.utc)
        with self._lock:
            eligible = [
                sample
                for sample in self.samples
                if 0 <= (sample.ts - start).total_seconds() <= tolerance_seconds
            ]
        if not eligible:
            raise ChainlinkOpenUnavailable(
                "Exact Chainlink window-open reference was not observed by this process."
            )
        return eligible[0].price

    def momentum_bps(self, seconds: float) -> float | None:
        with self._lock:
            samples = list(self.samples)
        if len(samples) < 2:
            return None
        latest = samples[-1]
        target = latest.ts.timestamp() - seconds
        older = [s for s in samples if s.ts.timestamp() <= target]
        if not older or older[-1].price <= 0:
            return None
        return math.log(latest.price / older[-1].price) * 10_000

    def sigma_per_sqrt_second(self) -> float:
        with self._lock:
            samples = list(self.samples)[-240:]
        normalized: list[float] = []
        for a, b in zip(samples, samples[1:]):
            dt = (b.ts - a.ts).total_seconds()
            if dt <= 0 or a.price <= 0 or b.price <= 0:
                continue
            normalized.append(math.log(b.price / a.price) / math.sqrt(dt))
        if len(normalized) < 8:
            # Conservative floor until enough Chainlink ticks accumulate.
            return 0.00004
        rms = math.sqrt(sum(x * x for x in normalized) / len(normalized))
        return max(rms, 0.000025)
