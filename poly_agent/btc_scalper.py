from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import requests
from pydantic import BaseModel, Field
from rich.console import Console
from rich.table import Table

from .config import SETTINGS, Settings
from .models import Market, ProbabilityEstimate
from .polymarket import fetch_current_btc_15m_market
from .research import ResearchProviderError, estimate_probability, resolve_provider
from .risk import decide_baseline_trade

CLOB = "https://clob.polymarket.com"
SCALP_LEDGER = Path("data/btc_scalps.jsonl")


class ScalpTrade(BaseModel):
    trade_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    market_id: str
    question: str
    side: Literal["YES", "NO"]
    side_label: str
    token_id: str
    entry_time: datetime
    window_end: datetime
    entry_price: float
    stake: float
    shares: float
    entry_fee: float
    model_fair_probability: float
    model_confidence: float
    peak_exit_bid: float
    peak_net_pnl: float
    status: Literal["OPEN", "CLOSED"] = "OPEN"
    exit_time: datetime | None = None
    exit_price: float | None = None
    exit_fee: float = 0.0
    exit_reason: str | None = None
    realized_pnl: float | None = None


def load_scalps() -> list[ScalpTrade]:
    if not SCALP_LEDGER.exists():
        return []
    trades: list[ScalpTrade] = []
    with SCALP_LEDGER.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                trades.append(ScalpTrade.model_validate(json.loads(line)))
    return trades


def save_scalps(trades: list[ScalpTrade]) -> None:
    SCALP_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with SCALP_LEDGER.open("w", encoding="utf-8") as f:
        for trade in trades:
            f.write(json.dumps(trade.model_dump(mode="json")) + "\n")


def _upsert_trade(trade: ScalpTrade) -> None:
    trades = load_scalps()
    for i, existing in enumerate(trades):
        if existing.trade_id == trade.trade_id:
            trades[i] = trade
            break
    else:
        trades.append(trade)
    save_scalps(trades)


def already_scalped_market(market_id: str) -> bool:
    return any(t.market_id == market_id for t in load_scalps())


def fetch_top_of_book(token_id: str) -> tuple[float, float]:
    """Return executable top-of-book (best bid, best ask) from the public CLOB."""
    response = requests.get(
        f"{CLOB}/book",
        params={"token_id": token_id},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    bids = payload.get("bids") or []
    asks = payload.get("asks") or []
    if not bids or not asks:
        raise RuntimeError("CLOB order book has no executable bid/ask.")
    best_bid = max(float(level["price"]) for level in bids)
    best_ask = min(float(level["price"]) for level in asks)
    return best_bid, best_ask


def taker_fee(shares: float, price: float, s: Settings = SETTINGS) -> float:
    """Estimated current crypto taker fee in USDC for paper simulation."""
    fee = shares * s.btc_crypto_taker_fee_rate * price * (1 - price)
    return round(max(fee, 0.0), 5)


def net_exit_pnl(trade: ScalpTrade, exit_bid: float, s: Settings = SETTINGS) -> tuple[float, float]:
    exit_fee = taker_fee(trade.shares, exit_bid, s)
    gross = trade.shares * (exit_bid - trade.entry_price)
    net = gross - trade.entry_fee - exit_fee
    return net, exit_fee


def _token_for_side(market: Market, side: str) -> tuple[str, str]:
    if side == "YES":
        token_id = market.positive_token_id
        label = market.positive_label
    else:
        token_id = market.negative_token_id
        label = market.negative_label
    if not token_id:
        raise RuntimeError(f"No CLOB token ID for {label}.")
    return token_id, label


def open_scalp(
    market: Market,
    estimate: ProbabilityEstimate,
    *,
    s: Settings = SETTINGS,
    console: Console | None = None,
) -> ScalpTrade:
    console = console or Console()
    decision = decide_baseline_trade(market, estimate, s.starting_bankroll, s)
    if decision.side == "PASS":
        raise RuntimeError("Baseline side selection returned PASS.")

    token_id, side_label = _token_for_side(market, decision.side)
    best_bid, best_ask = fetch_top_of_book(token_id)
    if not 0 < best_ask < 1:
        raise RuntimeError(f"Invalid executable ask {best_ask:.4f}.")

    stake = s.starting_bankroll * min(s.baseline_position_pct, s.max_position_pct)
    shares = stake / best_ask
    entry_fee = taker_fee(shares, best_ask, s)
    now = datetime.now(timezone.utc)
    end = market.end_date
    if end is None:
        raise RuntimeError("BTC market has no window end time.")
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    trade = ScalpTrade(
        market_id=market.id,
        question=market.question,
        side=decision.side,
        side_label=side_label,
        token_id=token_id,
        entry_time=now,
        window_end=end,
        entry_price=best_ask,
        stake=round(stake, 2),
        shares=shares,
        entry_fee=entry_fee,
        model_fair_probability=decision.fair_probability,
        model_confidence=estimate.confidence,
        peak_exit_bid=best_bid,
        peak_net_pnl=net_exit_pnl(
            ScalpTrade(
                market_id=market.id,
                question=market.question,
                side=decision.side,
                side_label=side_label,
                token_id=token_id,
                entry_time=now,
                window_end=end,
                entry_price=best_ask,
                stake=round(stake, 2),
                shares=shares,
                entry_fee=entry_fee,
                model_fair_probability=decision.fair_probability,
                model_confidence=estimate.confidence,
                peak_exit_bid=best_bid,
                peak_net_pnl=0.0,
            ),
            best_bid,
            s,
        )[0],
    )
    _upsert_trade(trade)
    console.print(
        f"[green]PAPER SCALP OPENED:[/green] {side_label} ${trade.stake:.2f} | "
        f"ask {best_ask:.3f} | bid {best_bid:.3f} | entry fee ${entry_fee:.2f}"
    )
    return trade


def close_scalp(
    trade: ScalpTrade,
    exit_bid: float,
    reason: str,
    *,
    s: Settings = SETTINGS,
) -> ScalpTrade:
    pnl, exit_fee = net_exit_pnl(trade, exit_bid, s)
    closed = trade.model_copy(
        update={
            "status": "CLOSED",
            "exit_time": datetime.now(timezone.utc),
            "exit_price": exit_bid,
            "exit_fee": exit_fee,
            "exit_reason": reason,
            "realized_pnl": round(pnl, 5),
            "peak_exit_bid": max(trade.peak_exit_bid, exit_bid),
            "peak_net_pnl": max(trade.peak_net_pnl, pnl),
        }
    )
    _upsert_trade(closed)
    return closed


def manage_scalp(
    trade: ScalpTrade,
    *,
    s: Settings = SETTINGS,
    console: Console | None = None,
) -> ScalpTrade:
    console = console or Console()
    peak_net = trade.peak_net_pnl
    last_bid: float | None = None

    while True:
        now = datetime.now(timezone.utc)
        seconds_left = (trade.window_end - now).total_seconds()
        try:
            bid, _ = fetch_top_of_book(trade.token_id)
        except Exception as exc:
            if seconds_left <= s.btc_scalp_force_exit_seconds:
                console.print(f"[yellow]Price fetch failed near expiry:[/yellow] {exc}")
                time.sleep(1)
                continue
            console.print(f"[dim]Transient book error: {exc}[/dim]")
            time.sleep(s.btc_scalp_poll_seconds)
            continue

        net, _ = net_exit_pnl(trade, bid, s)
        peak_net = max(peak_net, net)
        trade = trade.model_copy(
            update={
                "peak_exit_bid": max(trade.peak_exit_bid, bid),
                "peak_net_pnl": peak_net,
            }
        )
        _upsert_trade(trade)

        if last_bid != bid:
            console.print(
                f"[dim]{trade.side_label} bid {bid:.3f} | net if sold now ${net:+.2f} | "
                f"peak ${peak_net:+.2f} | {max(seconds_left, 0):.0f}s left[/dim]"
            )
            last_bid = bid

        reason: str | None = None
        if net >= s.btc_scalp_take_profit_usd:
            reason = "TAKE_PROFIT"
        elif (
            peak_net >= s.btc_scalp_trail_arm_usd
            and net > 0
            and net <= peak_net - s.btc_scalp_trail_giveback_usd
        ):
            reason = "TRAILING_PROFIT"
        elif net <= -abs(s.btc_scalp_stop_loss_usd):
            reason = "STOP_LOSS"
        elif seconds_left <= s.btc_scalp_force_exit_seconds:
            reason = "WINDOW_TIMEOUT"

        if reason:
            closed = close_scalp(trade, bid, reason, s=s)
            console.print(
                f"[bold green]PAPER SCALP CLOSED:[/bold green] {closed.side_label} | "
                f"{closed.entry_price:.3f} -> {closed.exit_price:.3f} | "
                f"NET ${closed.realized_pnl:+.2f} | {reason}"
            )
            return closed

        time.sleep(s.btc_scalp_poll_seconds)


def scalp_one_current_window(
    provider: str | None = None,
    *,
    s: Settings = SETTINGS,
    console: Console | None = None,
) -> ScalpTrade | None:
    console = console or Console()
    market = fetch_current_btc_15m_market()
    now = datetime.now(timezone.utc)
    end = market.end_date
    if end is None:
        raise RuntimeError("BTC 15m market is missing an end time.")
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    seconds_left = (end - now).total_seconds()

    if seconds_left < s.btc_scalp_min_entry_seconds:
        console.print(
            f"[yellow]Only {seconds_left:.0f}s remain in this window; skipping late entry.[/yellow]"
        )
        return None
    if already_scalped_market(market.id):
        console.print("[dim]This BTC 15m window is already in the scalp ledger.[/dim]")
        return None

    selected_provider = resolve_provider(provider, s)
    console.print(
        f"[bold]BTC 15m:[/bold] {market.question} | "
        f"{market.positive_label} {market.yes_price:.1%} / {market.negative_label} {market.no_price:.1%}"
    )
    console.print(f"[bold]Research provider:[/bold] {selected_provider}")
    try:
        estimate = estimate_probability(market, provider=selected_provider, s=s)
    except ResearchProviderError:
        raise

    trade = open_scalp(market, estimate, s=s, console=console)
    return manage_scalp(trade, s=s, console=console)


def scalp_metrics(trades: list[ScalpTrade]) -> dict[str, float | int]:
    closed = [t for t in trades if t.status == "CLOSED" and t.realized_pnl is not None]
    wins = [t for t in closed if (t.realized_pnl or 0) > 0]
    losses = [t for t in closed if (t.realized_pnl or 0) < 0]
    gross_wins = sum(t.realized_pnl or 0 for t in wins)
    gross_losses = abs(sum(t.realized_pnl or 0 for t in losses))
    total_fees = sum(t.entry_fee + t.exit_fee for t in closed)
    avg_hold = 0.0
    if closed:
        holds = [
            (t.exit_time - t.entry_time).total_seconds()
            for t in closed
            if t.exit_time is not None
        ]
        avg_hold = sum(holds) / len(holds) if holds else 0.0
    return {
        "trades": len(trades),
        "closed": len(closed),
        "open": sum(t.status == "OPEN" for t in trades),
        "wins": len(wins),
        "losses": len(losses),
        "hit_rate": len(wins) / len(closed) if closed else 0.0,
        "net_pnl": sum(t.realized_pnl or 0 for t in closed),
        "total_fees": total_fees,
        "profit_factor": gross_wins / gross_losses if gross_losses else (float("inf") if gross_wins else 0.0),
        "avg_hold_seconds": avg_hold,
        "paper_equity": SETTINGS.starting_bankroll + sum(t.realized_pnl or 0 for t in closed),
    }


def render_scalp_report(console: Console | None = None) -> dict[str, float | int]:
    console = console or Console()
    trades = load_scalps()
    metrics = scalp_metrics(trades)
    console.print("\n[bold]POLY SLUDGE BTC 15M SCALP SCOREBOARD[/bold]")
    console.print(
        f"Trades {metrics['trades']} | Closed {metrics['closed']} | Open {metrics['open']} | "
        f"Wins {metrics['wins']} | Losses {metrics['losses']}"
    )
    if int(metrics["closed"]) > 0:
        pf = metrics["profit_factor"]
        pf_text = "∞" if pf == float("inf") else f"{float(pf):.2f}"
        console.print(
            f"[bold]Hit rate:[/bold] {float(metrics['hit_rate']):.1%} | "
            f"[bold]Net P&L:[/bold] ${float(metrics['net_pnl']):+,.2f} | "
            f"[bold]Fees:[/bold] ${float(metrics['total_fees']):,.2f} | "
            f"[bold]Profit factor:[/bold] {pf_text} | "
            f"[bold]Avg hold:[/bold] {float(metrics['avg_hold_seconds']):.0f}s | "
            f"[bold]Paper equity:[/bold] ${float(metrics['paper_equity']):,.2f}"
        )

    table = Table(title="BTC 15m Scalps")
    table.add_column("Status")
    table.add_column("Side")
    table.add_column("Entry")
    table.add_column("Exit")
    table.add_column("Net")
    table.add_column("Reason")
    table.add_column("Question")
    for trade in reversed(trades[-30:]):
        table.add_row(
            trade.status,
            trade.side_label,
            f"{trade.entry_price:.3f}",
            "-" if trade.exit_price is None else f"{trade.exit_price:.3f}",
            "-" if trade.realized_pnl is None else f"${trade.realized_pnl:+.2f}",
            trade.exit_reason or "-",
            trade.question,
        )
    console.print(table)
    return metrics


def run_scalp_loop(
    provider: str | None = None,
    *,
    s: Settings = SETTINGS,
    console: Console | None = None,
) -> None:
    console = console or Console()
    console.print(
        "[bold yellow]BTC 15M SCALPER - PAPER MONEY ONLY[/bold yellow] | "
        "takes one trade per window, monitors executable bids, and exits early when rules fire. "
        "Press Ctrl+C to stop."
    )
    try:
        while True:
            render_scalp_report(console)
            try:
                market = fetch_current_btc_15m_market()
                now = datetime.now(timezone.utc)
                end = market.end_date
                if end is None:
                    raise RuntimeError("Current BTC window has no end time.")
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                seconds_left = (end - now).total_seconds()

                if already_scalped_market(market.id):
                    sleep_for = max(seconds_left + 3, 3)
                    console.print(
                        f"[dim]Current window already traded. Waiting {sleep_for / 60:.1f}m…[/dim]"
                    )
                    time.sleep(sleep_for)
                    continue

                if seconds_left < s.btc_scalp_min_entry_seconds:
                    sleep_for = max(seconds_left + 3, 3)
                    console.print(
                        f"[dim]Too late to enter ({seconds_left:.0f}s left). Waiting for next window…[/dim]"
                    )
                    time.sleep(sleep_for)
                    continue

                scalp_one_current_window(provider, s=s, console=console)
            except ResearchProviderError as exc:
                console.print(f"[red]Research failed:[/red] {exc}")
                time.sleep(5)
            except Exception as exc:
                console.print(f"[yellow]Scalp loop error:[/yellow] {exc}")
                time.sleep(5)
    except KeyboardInterrupt:
        console.print("\n[bold]BTC scalp loop stopped.[/bold]")
        render_scalp_report(console)
