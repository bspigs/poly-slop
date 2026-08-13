from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import requests
from pydantic import BaseModel, Field
from rich.console import Console

from .btc_scalper import taker_fee
from .btc_signal import BookSnapshot, normal_cdf
from .btc_tick import fetch_both_books
from .chainlink_feed import ChainlinkBtcFeed, ChainlinkOpenUnavailable, ChainlinkUnavailable
from .config import SETTINGS, Settings
from .models import Market
from .polymarket import btc_15m_slug, fetch_current_btc_15m_market, fetch_market_by_id

CLOB = "https://clob.polymarket.com"
V3_LEDGER = Path("data/btc_v3.jsonl")


class V3Trade(BaseModel):
    trade_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    market_id: str
    question: str
    strategy: Literal["PAIR_ARB", "MAKER_SCALP"]
    side: Literal["YES", "NO", "PAIR"]
    side_label: str
    token_id: str | None = None
    entry_time: datetime
    window_end: datetime
    entry_price: float
    target_exit_price: float | None = None
    stake: float
    shares: float
    entry_fee: float = 0.0
    fair_probability: float = 0.5
    edge_at_entry: float = 0.0
    status: Literal["OPEN", "CLOSED"] = "OPEN"
    exit_time: datetime | None = None
    exit_price: float | None = None
    exit_fee: float = 0.0
    exit_reason: str | None = None
    realized_pnl: float | None = None


@dataclass
class MakerQuote:
    market_id: str
    side: Literal["YES", "NO"]
    side_label: str
    token_id: str
    price: float
    fair_probability: float
    edge: float
    tick_size: float
    placed_at: datetime
    baseline_last_trade: tuple[float, str] | None


def load_v3() -> list[V3Trade]:
    if not V3_LEDGER.exists():
        return []
    out: list[V3Trade] = []
    with V3_LEDGER.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(V3Trade.model_validate(json.loads(line)))
            except Exception:
                continue
    return out


def _save_v3(trades: list[V3Trade]) -> None:
    V3_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with V3_LEDGER.open("w", encoding="utf-8") as f:
        for trade in trades:
            f.write(json.dumps(trade.model_dump(mode="json")) + "\n")


def _upsert_v3(trade: V3Trade) -> None:
    trades = load_v3()
    for i, existing in enumerate(trades):
        if existing.trade_id == trade.trade_id:
            trades[i] = trade
            break
    else:
        trades.append(trade)
    _save_v3(trades)


def v3_equity(s: Settings = SETTINGS) -> float:
    realized = sum(t.realized_pnl or 0.0 for t in load_v3() if t.status == "CLOSED")
    return max(s.starting_bankroll + realized, 0.0)


def _tick_size(book: BookSnapshot) -> float:
    # Polymarket changes tick size near the 0/1 extremes. For the liquid middle
    # of these 15m markets, one cent is the normal quote increment.
    if book.bid < 0.04 or book.ask > 0.96:
        return 0.001
    return 0.01


def fetch_last_trades(market: Market) -> dict[str, tuple[float, str]]:
    token_ids = [t for t in (market.positive_token_id, market.negative_token_id) if t]
    if len(token_ids) != 2:
        raise RuntimeError("BTC market is missing outcome token IDs.")
    response = requests.post(
        f"{CLOB}/last-trades-prices",
        json=[{"token_id": token_id} for token_id in token_ids],
        headers={"Cache-Control": "no-cache", "Content-Type": "application/json"},
        timeout=5,
    )
    response.raise_for_status()
    rows = response.json()
    result: dict[str, tuple[float, str]] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict):
                continue
            token = str(row.get("token_id") or "")
            try:
                price = float(row.get("price"))
            except (TypeError, ValueError):
                continue
            side = str(row.get("side") or "").upper()
            if token:
                result[token] = (price, side)
    return result


def _oracle_fair(
    feed: ChainlinkBtcFeed,
    window_start: datetime,
    window_end: datetime,
) -> tuple[float, float, float, float | None, float | None]:
    latest = feed.latest()
    open_price = feed.window_open_price(window_start)
    seconds_left = max((window_end - latest.ts).total_seconds(), 1.0)
    sigma = feed.sigma_per_sqrt_second()
    z = math.log(latest.price / open_price) / max(sigma * math.sqrt(seconds_left), 1e-9)
    base = min(max(normal_cdf(z), 0.01), 0.99)
    m15 = feed.momentum_bps(15)
    m30 = feed.momentum_bps(30)
    momentum = 0.0
    if m15 is not None:
        momentum += 0.65 * m15
    if m30 is not None:
        momentum += 0.35 * (m30 / 2.0)
    # Momentum is deliberately a small soft adjustment, not the core signal.
    fair_up = min(max(base + 0.02 * math.tanh(momentum / 5.0), 0.01), 0.99)
    distance_bps = math.log(latest.price / open_price) * 10_000
    return fair_up, latest.price, open_price, m15, m30


def _pair_arb(
    market: Market,
    up: BookSnapshot | None,
    down: BookSnapshot | None,
    *,
    s: Settings,
) -> tuple[V3Trade, tuple[float, float]] | None:
    if up is None or down is None:
        return None
    if not (0 < up.ask < 1 and 0 < down.ask < 1):
        return None

    # One UP + one DOWN share pays exactly $1 at resolution. This is the one
    # taker trade V3 likes: only execute when the combined asks plus both crypto
    # taker fees are already below $1 by a configured safety margin.
    fee_up = s.btc_crypto_taker_fee_rate * up.ask * (1.0 - up.ask)
    fee_down = s.btc_crypto_taker_fee_rate * down.ask * (1.0 - down.ask)
    locked_per_pair = 1.0 - up.ask - down.ask - fee_up - fee_down
    if locked_per_pair < s.btc_v3_arb_min_profit_per_pair:
        return None

    budget = min(s.btc_v3_arb_max_notional, v3_equity(s) * s.btc_v3_arb_max_equity_pct)
    pair_cost_before_fees = up.ask + down.ask
    shares = min(
        budget / max(pair_cost_before_fees + fee_up + fee_down, 1e-9),
        up.ask_size,
        down.ask_size,
    )
    if shares <= 0:
        return None
    total_fee = shares * (fee_up + fee_down)
    stake = shares * pair_cost_before_fees + total_fee
    pnl = shares * locked_per_pair
    end = market.end_date or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    trade = V3Trade(
        market_id=market.id,
        question=market.question,
        strategy="PAIR_ARB",
        side="PAIR",
        side_label="UP+DOWN",
        entry_time=datetime.now(timezone.utc),
        window_end=end,
        entry_price=pair_cost_before_fees,
        stake=stake,
        shares=shares,
        entry_fee=total_fee,
        fair_probability=1.0,
        edge_at_entry=locked_per_pair,
        status="CLOSED",
        exit_time=end,
        exit_price=1.0,
        exit_fee=0.0,
        exit_reason="LOCKED_COMPLETE_SET_ARB",
        realized_pnl=round(pnl, 5),
    )
    return trade, (up.ask, down.ask)


def _maker_candidate(
    market: Market,
    up: BookSnapshot | None,
    down: BookSnapshot | None,
    fair_up: float,
    last_trades: dict[str, tuple[float, str]],
    *,
    s: Settings,
) -> MakerQuote | None:
    candidates: list[MakerQuote] = []
    now = datetime.now(timezone.utc)
    for side, label, token, fair, book in (
        ("YES", market.positive_label, market.positive_token_id, fair_up, up),
        ("NO", market.negative_label, market.negative_token_id, 1.0 - fair_up, down),
    ):
        if token is None or book is None:
            continue
        tick = _tick_size(book)
        # We only simulate maker fills when we can improve the bid by at least
        # one tick without crossing the ask. This gives our hypothetical quote
        # top-of-queue priority at its new price and avoids optimistic queue fills.
        quote_price = round(book.bid + tick, 3)
        if quote_price >= book.ask - 1e-9:
            continue
        if book.spread + 1e-9 < s.btc_v3_min_spread_ticks * tick:
            continue
        if not s.btc_v3_min_contract_price <= quote_price <= s.btc_v3_max_contract_price:
            continue
        edge = fair - quote_price
        if edge < s.btc_v3_maker_min_edge:
            continue
        candidates.append(
            MakerQuote(
                market_id=market.id,
                side=side,
                side_label=label,
                token_id=token,
                price=quote_price,
                fair_probability=fair,
                edge=edge,
                tick_size=tick,
                placed_at=now,
                baseline_last_trade=last_trades.get(token),
            )
        )
    return max(candidates, key=lambda q: q.edge) if candidates else None


def _maker_bid_filled(quote: MakerQuote, last_trades: dict[str, tuple[float, str]]) -> bool:
    current = last_trades.get(quote.token_id)
    if current is None or current == quote.baseline_last_trade:
        return False
    price, side = current
    # A subsequent SELL trade at/through our improved bid is conservative public
    # evidence that a resting maker bid at that price could have been hit.
    return side == "SELL" and price <= quote.price + 1e-9


def _open_maker_trade(
    market: Market,
    quote: MakerQuote,
    *,
    s: Settings,
) -> V3Trade:
    equity = v3_equity(s)
    stake = round(equity * s.btc_v3_maker_position_pct, 2)
    shares = stake / quote.price
    end = market.end_date or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    target = min(round(quote.price + quote.tick_size * s.btc_v3_target_ticks, 3), 0.999)
    trade = V3Trade(
        market_id=market.id,
        question=market.question,
        strategy="MAKER_SCALP",
        side=quote.side,
        side_label=quote.side_label,
        token_id=quote.token_id,
        entry_time=datetime.now(timezone.utc),
        window_end=end,
        entry_price=quote.price,
        target_exit_price=target,
        stake=stake,
        shares=shares,
        entry_fee=0.0,
        fair_probability=quote.fair_probability,
        edge_at_entry=quote.edge,
    )
    _upsert_v3(trade)
    return trade


def _close_v3(
    trade: V3Trade,
    exit_price: float,
    reason: str,
    *,
    s: Settings,
    taker_exit: bool,
) -> V3Trade:
    exit_fee = taker_fee(trade.shares, exit_price, s) if taker_exit else 0.0
    pnl = trade.shares * (exit_price - trade.entry_price) - trade.entry_fee - exit_fee
    closed = trade.model_copy(
        update={
            "status": "CLOSED",
            "exit_time": datetime.now(timezone.utc),
            "exit_price": exit_price,
            "exit_fee": exit_fee,
            "exit_reason": reason,
            "realized_pnl": round(pnl, 5),
        }
    )
    _upsert_v3(closed)
    return closed


def recover_v3(console: Console | None = None) -> int:
    console = console or Console()
    now = datetime.now(timezone.utc)
    recovered = 0
    for trade in load_v3():
        if trade.status != "OPEN" or trade.window_end > now or trade.strategy != "MAKER_SCALP":
            continue
        try:
            market = fetch_market_by_id(trade.market_id)
        except Exception:
            continue
        winner: str | None = None
        if market.closed and market.yes_price >= 0.999:
            winner = "YES"
        elif market.closed and market.no_price >= 0.999:
            winner = "NO"
        if winner is None:
            continue
        settlement = 1.0 if trade.side == winner else 0.0
        closed = _close_v3(trade, settlement, "RECOVERED_AT_RESOLUTION", s=SETTINGS, taker_exit=False)
        console.print(f"[yellow]Recovered V3 maker trade:[/yellow] {closed.side_label} ${closed.realized_pnl:+.2f}")
        recovered += 1
    return recovered


def v3_metrics(s: Settings = SETTINGS) -> dict[str, float | int]:
    trades = load_v3()
    closed = [t for t in trades if t.status == "CLOSED" and t.realized_pnl is not None]
    maker = [t for t in closed if t.strategy == "MAKER_SCALP"]
    arb = [t for t in closed if t.strategy == "PAIR_ARB"]
    wins = [t for t in closed if (t.realized_pnl or 0.0) > 0]
    losses = [t for t in closed if (t.realized_pnl or 0.0) < 0]
    fees = sum(t.entry_fee + t.exit_fee for t in closed)
    pnl = sum(t.realized_pnl or 0.0 for t in closed)
    return {
        "trades": len(trades),
        "closed": len(closed),
        "maker": len(maker),
        "arb": len(arb),
        "wins": len(wins),
        "losses": len(losses),
        "hit_rate": len(wins) / len(closed) if closed else 0.0,
        "fees": fees,
        "net_pnl": pnl,
        "equity": s.starting_bankroll + pnl,
    }


def render_v3_report(console: Console | None = None, s: Settings = SETTINGS) -> dict[str, float | int]:
    console = console or Console()
    recover_v3(console)
    m = v3_metrics(s)
    console.print("\n[bold]POLY SLUDGE BTC V3 MAKER/ARB SCOREBOARD[/bold]")
    console.print(
        f"Trades {m['trades']} | Closed {m['closed']} | maker {m['maker']} | arb {m['arb']} | "
        f"{m['wins']}W/{m['losses']}L | hit {float(m['hit_rate']):.1%}"
    )
    console.print(
        f"V3 net ${float(m['net_pnl']):+,.2f} | fees ${float(m['fees']):,.2f} | "
        f"V3 standalone equity ${float(m['equity']):,.2f}"
    )
    return m


def run_v3_loop(*, s: Settings = SETTINGS, console: Console | None = None) -> None:
    console = console or Console()
    recover_v3(console)
    feed = ChainlinkBtcFeed()
    console.print(
        "[bold yellow]BTC V3 1HZ MAKER/ARB SCANNER - PAPER ONLY[/bold yellow] | "
        "Chainlink BTC/USD + both Polymarket books every second. Taker churn is OFF. "
        "Maker entries/exits are fee-free; taker exits are emergency-only."
    )

    current_market_id: str | None = None
    quote: MakerQuote | None = None
    active: V3Trade | None = next((t for t in reversed(load_v3()) if t.status == "OPEN"), None)
    last_exit_baseline: tuple[float, str] | None = None
    last_arb_fingerprint: tuple[float, float] | None = None
    last_arb_time = 0.0

    try:
        while True:
            tick_started = time.monotonic()
            try:
                market = fetch_current_btc_15m_market()
                _, window_start, window_end = btc_15m_slug(datetime.now(timezone.utc))
                seconds_left = (window_end - datetime.now(timezone.utc)).total_seconds()
                if market.id != current_market_id:
                    current_market_id = market.id
                    quote = None
                    last_arb_fingerprint = None
                    console.print(f"\n[bold]NEW BTC WINDOW[/bold] | {market.question} | {max(seconds_left, 0):.0f}s left")

                up, down = fetch_both_books(market)
                last_trades = fetch_last_trades(market)

                if active is not None and active.status == "OPEN":
                    token_book = up if active.side == "YES" else down
                    if token_book is None:
                        console.print("[dim]V3 POSITION | book temporarily unavailable[/dim]")
                    else:
                        current_last = last_trades.get(active.token_id or "")
                        target = active.target_exit_price or active.entry_price
                        maker_tp = (
                            current_last is not None
                            and current_last != last_exit_baseline
                            and current_last[1] == "BUY"
                            and current_last[0] >= target - 1e-9
                        )
                        fair_side: float | None = None
                        try:
                            fair_up, chainlink, open_price, _, _ = _oracle_fair(feed, window_start, window_end)
                            fair_side = fair_up if active.side == "YES" else 1.0 - fair_up
                        except (ChainlinkUnavailable, ChainlinkOpenUnavailable):
                            chainlink = open_price = 0.0

                        if maker_tp:
                            active = _close_v3(active, target, "MAKER_TAKE_PROFIT", s=s, taker_exit=False)
                            console.print(
                                f"[bold green]V3 MAKER WIN:[/bold green] {active.side_label} | "
                                f"{active.entry_price:.3f}->{target:.3f} | NET ${active.realized_pnl:+.2f} | fee $0.00"
                            )
                            active = None
                            last_exit_baseline = None
                        else:
                            hold = (datetime.now(timezone.utc) - active.entry_time).total_seconds()
                            emergency = False
                            reason = ""
                            if seconds_left <= s.btc_v3_force_exit_seconds:
                                emergency, reason = True, "WINDOW_EXIT"
                            elif (
                                fair_side is not None
                                and hold >= s.btc_v3_min_hold_seconds
                                and fair_side < active.entry_price - s.btc_v3_stop_fair_gap
                            ):
                                emergency, reason = True, "FAIR_VALUE_BREAK"
                            if emergency:
                                active = _close_v3(active, token_book.bid, reason, s=s, taker_exit=True)
                                console.print(
                                    f"[yellow]V3 EMERGENCY EXIT:[/yellow] {active.side_label} | "
                                    f"NET ${active.realized_pnl:+.2f} | fee ${active.exit_fee:.2f} | {reason}"
                                )
                                active = None
                                last_exit_baseline = None
                            else:
                                fair_text = "?" if fair_side is None else f"{fair_side:.1%}"
                                console.print(
                                    f"V3 POSITION | {active.side_label} maker {active.entry_price:.3f} | "
                                    f"target {target:.3f} | bid {token_book.bid:.3f} | fair {fair_text} | "
                                    f"{max(seconds_left, 0):.0f}s left"
                                )
                                if last_exit_baseline is None:
                                    last_exit_baseline = current_last

                    elapsed = time.monotonic() - tick_started
                    time.sleep(max(s.btc_v3_tick_seconds - elapsed, 0.0))
                    continue

                # First priority: a true complete-set pricing error. This is the
                # only routine taker path because its profit is already locked.
                arb_result = _pair_arb(market, up, down, s=s)
                if arb_result is not None:
                    arb_trade, fingerprint = arb_result
                    now_mono = time.monotonic()
                    if (
                        fingerprint != last_arb_fingerprint
                        or now_mono - last_arb_time >= s.btc_v3_arb_cooldown_seconds
                    ):
                        _upsert_v3(arb_trade)
                        last_arb_fingerprint = fingerprint
                        last_arb_time = now_mono
                        console.print(
                            f"[bold green]V3 LOCKED ARB:[/bold green] UP {fingerprint[0]:.3f} + "
                            f"DOWN {fingerprint[1]:.3f} | {arb_trade.shares:.2f} pairs | "
                            f"NET ${arb_trade.realized_pnl:+.2f} after ${arb_trade.entry_fee:.2f} fees"
                        )

                fair_up: float | None = None
                chainlink = open_price = 0.0
                try:
                    fair_up, chainlink, open_price, m15, m30 = _oracle_fair(feed, window_start, window_end)
                except ChainlinkOpenUnavailable:
                    # Starting mid-window: still scan complete-set arbitrage now,
                    # but do not fake the price-to-beat. Directional maker quotes
                    # begin automatically at the next window boundary we observe.
                    quote = None
                    console.print(
                        "V3 TICK | exact Chainlink price-to-beat not observed yet; "
                        "ARB scan active, maker direction starts next 15m window"
                    )
                    elapsed = time.monotonic() - tick_started
                    time.sleep(max(s.btc_v3_tick_seconds - elapsed, 0.0))
                    continue
                except ChainlinkUnavailable:
                    quote = None
                    console.print("V3 TICK | waiting for fresh Chainlink BTC/USD stream")
                    elapsed = time.monotonic() - tick_started
                    time.sleep(max(s.btc_v3_tick_seconds - elapsed, 0.0))
                    continue

                desired = _maker_candidate(market, up, down, fair_up, last_trades, s=s)

                if quote is not None:
                    quote_age = (datetime.now(timezone.utc) - quote.placed_at).total_seconds()
                    still_desired = (
                        desired is not None
                        and desired.side == quote.side
                        and abs(desired.price - quote.price) < 1e-9
                        and desired.edge >= s.btc_v3_maker_min_edge
                    )
                    if _maker_bid_filled(quote, last_trades):
                        active = _open_maker_trade(market, quote, s=s)
                        last_exit_baseline = last_trades.get(active.token_id or "")
                        console.print(
                            f"[bold green]V3 MAKER FILL:[/bold green] {active.side_label} @ {active.entry_price:.3f} | "
                            f"target {active.target_exit_price:.3f} | edge {active.edge_at_entry:+.1%} | entry fee $0.00"
                        )
                        quote = None
                    elif quote_age >= s.btc_v3_quote_lifetime_seconds or not still_desired:
                        console.print(
                            f"V3 CANCEL | {quote.side_label} bid {quote.price:.3f} | "
                            f"age {quote_age:.1f}s | stale/no longer best edge"
                        )
                        quote = None

                if active is None and quote is None and desired is not None and seconds_left > s.btc_v3_force_exit_seconds:
                    quote = desired
                    console.print(
                        f"[cyan]V3 MAKER QUOTE:[/cyan] {quote.side_label} BID {quote.price:.3f} | "
                        f"fair {quote.fair_probability:.1%} | edge {quote.edge:+.1%} | "
                        f"Chainlink ${chainlink:,.2f} vs beat ${open_price:,.2f}"
                    )
                elif active is None and quote is None:
                    up_text = "-" if up is None else f"{up.bid:.3f}/{up.ask:.3f}"
                    down_text = "-" if down is None else f"{down.bid:.3f}/{down.ask:.3f}"
                    console.print(
                        f"V3 TICK | Chainlink ${chainlink:,.2f} vs beat ${open_price:,.2f} | "
                        f"P(up) {fair_up:.1%} | UP {up_text} | DOWN {down_text} | no maker edge"
                    )

            except KeyboardInterrupt:
                raise
            except Exception as exc:
                console.print(f"[yellow]V3 tick issue:[/yellow] {exc}")

            elapsed = time.monotonic() - tick_started
            time.sleep(max(s.btc_v3_tick_seconds - elapsed, 0.0))
    except KeyboardInterrupt:
        console.print("\n[bold]BTC V3 stopped.[/bold]")
        render_v3_report(console, s)
    finally:
        feed.close()
