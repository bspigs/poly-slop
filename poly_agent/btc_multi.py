from __future__ import annotations

import time
from datetime import datetime, timezone

from rich.console import Console

from .btc_scalper import (
    ScalpTrade,
    load_scalps,
    manage_scalp,
    open_scalp,
    recover_expired_open_scalps,
    scalp_metrics,
)
from .config import SETTINGS, Settings
from .polymarket import fetch_current_btc_15m_market
from .research import ResearchProviderError, estimate_probability, resolve_provider


def _open_trade_for_market(market_id: str) -> ScalpTrade | None:
    """Return the currently open scalp for a market, if one exists."""
    for trade in reversed(load_scalps()):
        if trade.market_id == market_id and trade.status == "OPEN":
            return trade
    return None


def _trade_count_for_market(market_id: str) -> int:
    return sum(trade.market_id == market_id for trade in load_scalps())


def scalp_next_in_current_window(
    provider: str | None = None,
    *,
    s: Settings = SETTINGS,
    console: Console | None = None,
) -> ScalpTrade | None:
    """Run or resume one scalp, while allowing unlimited sequential re-entry."""
    console = console or Console()
    recover_expired_open_scalps(console)
    market = fetch_current_btc_15m_market()

    now = datetime.now(timezone.utc)
    end = market.end_date
    if end is None:
        raise RuntimeError("BTC 15m market is missing an end time.")
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    seconds_left = (end - now).total_seconds()

    existing = _open_trade_for_market(market.id)
    if existing is not None:
        console.print(
            f"[yellow]Resuming open paper scalp:[/yellow] {existing.side_label} | "
            f"entry {existing.entry_price:.3f}"
        )
        return manage_scalp(existing, s=s, console=console)

    if seconds_left < s.btc_scalp_min_entry_seconds:
        return None

    selected_provider = resolve_provider(provider, s)
    trade_number = _trade_count_for_market(market.id) + 1
    console.print(
        f"\n[bold cyan]BTC WINDOW TRADE #{trade_number}[/bold cyan] | "
        f"{seconds_left:.0f}s left | {market.positive_label} {market.yes_price:.1%} / "
        f"{market.negative_label} {market.no_price:.1%}"
    )
    console.print(f"[bold]Research provider:[/bold] {selected_provider}")

    estimate = estimate_probability(market, provider=selected_provider, s=s)
    trade = open_scalp(market, estimate, s=s, console=console)
    return manage_scalp(trade, s=s, console=console)


def run_multi_scalp_loop(
    provider: str | None = None,
    *,
    s: Settings = SETTINGS,
    console: Console | None = None,
) -> None:
    """Continuously scalp repeatedly inside every BTC 15-minute window."""
    console = console or Console()
    console.print(
        "[bold yellow]BTC 15M MULTI-SCALPER - PAPER MONEY ONLY[/bold yellow] | "
        "unlimited sequential trades per window; one open position at a time. "
        "Press Ctrl+C to stop."
    )

    current_market_id: str | None = None
    try:
        while True:
            try:
                recover_expired_open_scalps(console)
                market = fetch_current_btc_15m_market()
                now = datetime.now(timezone.utc)
                end = market.end_date
                if end is None:
                    raise RuntimeError("Current BTC window has no end time.")
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                seconds_left = (end - now).total_seconds()

                if market.id != current_market_id:
                    current_market_id = market.id
                    console.print(
                        f"\n[bold]NEW BTC 15M WINDOW[/bold] | {market.question} | "
                        f"{max(seconds_left, 0):.0f}s remaining"
                    )

                existing = _open_trade_for_market(market.id)
                if existing is None and seconds_left < s.btc_scalp_min_entry_seconds:
                    sleep_for = max(seconds_left + 2, 2)
                    console.print(
                        f"[dim]Entry cutoff reached ({seconds_left:.0f}s left). "
                        f"Waiting {sleep_for:.0f}s for the next window…[/dim]"
                    )
                    time.sleep(sleep_for)
                    continue

                result = scalp_next_in_current_window(provider, s=s, console=console)
                if result is not None and result.status == "CLOSED":
                    metrics = scalp_metrics(load_scalps(), s)
                    console.print(
                        f"[bold]RUNNING SCALP STATS:[/bold] "
                        f"{metrics['wins']}W/{metrics['losses']}L | "
                        f"hit {float(metrics['hit_rate']):.1%} | "
                        f"net ${float(metrics['net_pnl']):+,.2f} | "
                        f"fees ${float(metrics['total_fees']):,.2f} | "
                        f"equity ${float(metrics['paper_equity']):,.2f}"
                    )

                    now_after = datetime.now(timezone.utc)
                    seconds_after = (end - now_after).total_seconds()
                    if seconds_after >= s.btc_scalp_min_entry_seconds:
                        console.print(
                            f"[dim]Re-entry enabled: {seconds_after:.0f}s remain in this same window.[/dim]"
                        )
                        time.sleep(0.5)
                    continue

                time.sleep(0.5)

            except KeyboardInterrupt:
                raise
            except ResearchProviderError as exc:
                console.print(f"[red]Research failed:[/red] {exc}")
                time.sleep(3)
            except Exception as exc:
                console.print(f"[yellow]Multi-scalp loop error:[/yellow] {exc}")
                time.sleep(3)

    except KeyboardInterrupt:
        console.print("\n[bold]BTC multi-scalp loop stopped.[/bold]")
        metrics = scalp_metrics(load_scalps(), s)
        console.print(
            f"Final: {metrics['wins']}W/{metrics['losses']}L | "
            f"hit {float(metrics['hit_rate']):.1%} | "
            f"net ${float(metrics['net_pnl']):+,.2f} | "
            f"fees ${float(metrics['total_fees']):,.2f} | "
            f"equity ${float(metrics['paper_equity']):,.2f}"
        )
