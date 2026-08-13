from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rich.console import Console
from rich.table import Table

from .config import SETTINGS
from .models import Market, PaperPosition
from .paper import load_positions
from .polymarket import fetch_market_by_id

Status = Literal["WIN", "LOSS", "PENDING", "ERROR"]


@dataclass
class PaperResult:
    position: PaperPosition
    status: Status
    winning_outcome: str | None = None
    pnl: float = 0.0
    brier: float | None = None
    error: str | None = None


def resolved_winner(market: Market) -> str | None:
    """Infer a finalized binary winner only when Gamma shows a closed 1/0 market."""
    if not market.closed:
        return None
    if market.yes_price >= 0.999 and market.no_price <= 0.001:
        return "YES"
    if market.no_price >= 0.999 and market.yes_price <= 0.001:
        return "NO"
    return None


def settle_position(position: PaperPosition, market: Market) -> PaperResult:
    winner = resolved_winner(market)
    if winner is None:
        return PaperResult(position=position, status="PENDING")

    won = position.side == winner
    payout = position.shares if won else 0.0
    pnl = payout - position.stake

    fair_yes = (
        position.fair_probability
        if position.side == "YES"
        else 1 - position.fair_probability
    )
    observed_yes = 1.0 if winner == "YES" else 0.0
    brier = (fair_yes - observed_yes) ** 2

    return PaperResult(
        position=position,
        status="WIN" if won else "LOSS",
        winning_outcome=winner,
        pnl=pnl,
        brier=brier,
    )


def build_report() -> tuple[list[PaperResult], dict[str, float | int]]:
    positions = load_positions()
    results: list[PaperResult] = []
    market_cache: dict[str, Market] = {}

    for position in positions:
        try:
            market = market_cache.get(position.market_id)
            if market is None:
                market = fetch_market_by_id(position.market_id)
                market_cache[position.market_id] = market
            results.append(settle_position(position, market))
        except Exception as exc:
            results.append(
                PaperResult(
                    position=position,
                    status="ERROR",
                    error=str(exc),
                )
            )

    resolved = [r for r in results if r.status in {"WIN", "LOSS"}]
    wins = sum(r.status == "WIN" for r in resolved)
    losses = sum(r.status == "LOSS" for r in resolved)
    pending = sum(r.status == "PENDING" for r in results)
    errors = sum(r.status == "ERROR" for r in results)
    realized_pnl = sum(r.pnl for r in resolved)
    resolved_stake = sum(r.position.stake for r in resolved)
    briers = [r.brier for r in resolved if r.brier is not None]

    metrics: dict[str, float | int] = {
        "trades": len(results),
        "resolved": len(resolved),
        "wins": wins,
        "losses": losses,
        "pending": pending,
        "errors": errors,
        "hit_rate": wins / len(resolved) if resolved else 0.0,
        "realized_pnl": realized_pnl,
        "roi_on_resolved_stake": realized_pnl / resolved_stake if resolved_stake else 0.0,
        "brier_score": sum(briers) / len(briers) if briers else 0.0,
        "paper_equity": SETTINGS.starting_bankroll + realized_pnl,
    }
    return results, metrics


def render_report(console: Console | None = None) -> dict[str, float | int]:
    console = console or Console()
    results, metrics = build_report()

    console.print("\n[bold]POLY SLUDGE PAPER SCOREBOARD[/bold]")
    if not results:
        console.print("[yellow]No paper trades yet. Run baseline paper mode first.[/yellow]")
        return metrics

    console.print(
        f"Trades {metrics['trades']} | Resolved {metrics['resolved']} | "
        f"Wins {metrics['wins']} | Losses {metrics['losses']} | "
        f"Pending {metrics['pending']}"
    )

    if int(metrics["resolved"]) > 0:
        console.print(
            f"[bold]Hit rate:[/bold] {float(metrics['hit_rate']):.1%} | "
            f"[bold]Realized P&L:[/bold] ${float(metrics['realized_pnl']):+,.2f} | "
            f"[bold]ROI on resolved stake:[/bold] {float(metrics['roi_on_resolved_stake']):+.1%} | "
            f"[bold]Brier score:[/bold] {float(metrics['brier_score']):.3f} | "
            f"[bold]Paper equity:[/bold] ${float(metrics['paper_equity']):,.2f}"
        )
    else:
        console.print("[dim]No positions have resolved yet, so success rate is not available yet.[/dim]")

    table = Table(title="Paper Trades")
    table.add_column("Status")
    table.add_column("Side")
    table.add_column("Entry")
    table.add_column("Stake")
    table.add_column("P&L")
    table.add_column("Question")

    for result in reversed(results[-30:]):
        pnl = "-" if result.status not in {"WIN", "LOSS"} else f"${result.pnl:+,.2f}"
        table.add_row(
            result.status,
            result.position.side,
            f"{result.position.entry_price:.1%}",
            f"${result.position.stake:,.2f}",
            pnl,
            result.position.question,
        )
    console.print(table)

    if int(metrics["errors"]) > 0:
        console.print(
            f"[yellow]{metrics['errors']} market lookup(s) failed; those trades were excluded from statistics.[/yellow]"
        )

    return metrics
