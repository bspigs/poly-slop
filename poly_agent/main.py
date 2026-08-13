from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

from rich.console import Console
from rich.table import Table

from .config import SETTINGS
from .paper import already_traded_market, record
from .polymarket import (
    btc_15m_slug,
    fetch_current_btc_15m_market,
    fetch_markets,
    load_markets_from_file,
)
from .report import render_report
from .research import ResearchProviderError, estimate_probability, resolve_provider
from .risk import decide_baseline_trade, decide_trade
from .scanner import days_to_resolution, rank_markets

console = Console()


def _side_label(side: str, positive_label: str, negative_label: str) -> str:
    if side == "YES":
        return positive_label
    if side == "NO":
        return negative_label
    return side


def _resolves_text(market, demo: bool = False) -> str:
    if demo:
        return "demo"
    if market.end_date is None:
        return "?"
    now = datetime.now(timezone.utc)
    end = market.end_date
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    seconds = max((end - now).total_seconds(), 0)
    if seconds <= 3600:
        return f"{seconds / 60:.1f}m"
    days = days_to_resolution(market)
    return f"{days:.1f}d" if days is not None else "?"


def run(
    max_research: int = 5,
    execute_paper: bool = False,
    baseline_paper: bool = False,
    demo: bool = False,
    provider: str | None = None,
    btc_15m: bool = False,
) -> None:
    if demo:
        console.print("[bold]Loading synthetic demo markets…[/bold]")
        markets = load_markets_from_file("data/demo_markets.json")
    elif btc_15m:
        console.print("[bold]Fetching current Bitcoin Up/Down 15-minute market…[/bold]")
        try:
            market = fetch_current_btc_15m_market()
            markets = [market]
        except Exception as exc:
            console.print(f"[red]BTC 15m fetch failed:[/red] {exc}")
            return
    else:
        console.print("[bold]Fetching Polymarket markets…[/bold]")
        console.print(
            f"[dim]Resolution window: {SETTINGS.min_days_to_resolution:g} to "
            f"{SETTINGS.max_days_to_resolution:g} days[/dim]"
        )
        try:
            markets = fetch_markets(limit=max(100, SETTINGS.max_markets * 4))
        except Exception as exc:
            console.print(f"[red]Market fetch failed:[/red] {exc}")
            console.print("Run `python -m poly_agent.main --demo --research 0` to verify the local scanner without network access.")
            return

    ranked = markets if btc_15m else rank_markets(markets, enforce_resolution_window=not demo)

    table = Table(title="BTC 15m Candidate" if btc_15m else "Top Scanner Candidates")
    table.add_column("Price")
    table.add_column("Resolves")
    table.add_column("Liquidity")
    table.add_column("Volume")
    table.add_column("Question")
    for m in ranked[:15]:
        table.add_row(
            f"{m.positive_label} {m.yes_price:.1%}",
            _resolves_text(m, demo),
            f"${m.liquidity:,.0f}",
            f"${m.volume:,.0f}",
            m.question,
        )
    console.print(table)

    if not ranked:
        if not demo and not btc_15m:
            console.print(
                "[yellow]No markets passed the current short-term filters. "
                "Increase MAX_DAYS_TO_RESOLUTION in .env if needed.[/yellow]"
            )
        return

    if max_research <= 0:
        return

    try:
        selected_provider = resolve_provider(provider)
    except ResearchProviderError as exc:
        console.print(f"\n[red]Research provider error:[/red] {exc}")
        return

    if selected_provider == "openai" and not SETTINGS.openai_api_key:
        console.print("\n[red]OpenAI provider selected but OPENAI_API_KEY is not set.[/red]")
        return

    console.print(f"\n[bold]Research provider:[/bold] {selected_provider}")
    if baseline_paper:
        fake_stake = SETTINGS.starting_bankroll * min(
            SETTINGS.baseline_position_pct, SETTINGS.max_position_pct
        )
        console.print(
            f"[bold yellow]BASELINE PAPER MODE[/bold yellow] - simulation only; "
            f"records one ${fake_stake:,.2f} paper position per new market."
        )
    elif execute_paper:
        console.print(
            "[bold yellow]CONSERVATIVE PAPER MODE[/bold yellow] - simulation only; "
            "records only trades that pass confidence/edge gates."
        )

    research_queue = ranked
    if baseline_paper:
        research_queue = [m for m in ranked if not already_traded_market(m.id)]
        skipped = len(ranked) - len(research_queue)
        if skipped:
            console.print(f"[dim]Skipped {skipped} market(s) already in the paper ledger.[/dim]")
        if not research_queue:
            console.print("[yellow]Current market is already in the paper ledger.[/yellow]")
            return

    bankroll = SETTINGS.starting_bankroll

    for market in research_queue[:max_research]:
        console.print(f"\n[bold cyan]Researching:[/bold cyan] {market.question}")
        try:
            estimate = estimate_probability(market, provider=selected_provider)
        except ResearchProviderError as exc:
            console.print(f"[red]Research failed:[/red] {exc}")
            return

        decision = (
            decide_baseline_trade(market, estimate, bankroll)
            if baseline_paper
            else decide_trade(market, estimate, bankroll)
        )
        decision_label = _side_label(
            decision.side, decision.positive_label, decision.negative_label
        )
        console.print(
            f"Market {market.positive_label} {market.yes_price:.1%} | "
            f"fair {market.positive_label} {estimate.fair_yes_probability:.1%} | "
            f"confidence {estimate.confidence:.0%} | decision [bold]{decision_label}[/bold] | "
            f"edge {decision.edge:.1%} | stake ${decision.stake:,.2f}"
        )
        console.print(estimate.thesis)

        if execute_paper or baseline_paper:
            position = record(decision)
            if position:
                position_label = _side_label(
                    position.side, position.positive_label, position.negative_label
                )
                console.print(
                    f"[green]PAPER TRADE EXECUTED: {position_label} "
                    f"${position.stake:,.2f} at {position.entry_price:.1%}[/green]"
                )
            else:
                console.print("[yellow]No paper trade recorded for this market.[/yellow]")


def run_btc_15m_loop(provider: str | None = None) -> None:
    """Continuously take one baseline paper position in each BTC 15-minute window."""
    console.print(
        "[bold yellow]BTC 15-MINUTE LOOP[/bold yellow] - PAPER MONEY ONLY. "
        "Press Ctrl+C to stop."
    )
    try:
        while True:
            try:
                market = fetch_current_btc_15m_market()
            except Exception as exc:
                console.print(f"[yellow]Waiting for BTC 15m market:[/yellow] {exc}")
                time.sleep(5)
                continue

            if not already_traded_market(market.id):
                render_report(console)
                run(
                    max_research=1,
                    baseline_paper=True,
                    provider=provider,
                    btc_15m=True,
                )
            else:
                console.print(
                    f"[dim]Already traded current window: {market.question}[/dim]"
                )

            now = datetime.now(timezone.utc)
            end = market.end_date or btc_15m_slug(now)[2]
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            sleep_seconds = max((end - now).total_seconds() + 5, 5)
            console.print(
                f"[dim]Waiting {sleep_seconds / 60:.1f} minutes for the next BTC window…[/dim]"
            )
            time.sleep(sleep_seconds)
    except KeyboardInterrupt:
        console.print("\n[bold]BTC 15-minute paper loop stopped.[/bold]")
        render_report(console)


def cli() -> None:
    parser = argparse.ArgumentParser(description="Polymarket research and paper-trading agent")
    parser.add_argument("--research", type=int, default=5, help="How many top markets to research")
    parser.add_argument("--provider", choices=["auto", "openai", "ollama"], default=SETTINGS.research_provider)
    parser.add_argument("--paper", action="store_true", help="Record qualifying simulated trades")
    parser.add_argument(
        "--paper-baseline",
        action="store_true",
        help="Record a small fixed simulated trade for every new researched market, bypassing normal confidence/edge gates",
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="Check paper markets for resolution and show hit rate, P&L, ROI, and Brier score",
    )
    parser.add_argument(
        "--btc-15m",
        action="store_true",
        help="Target only the currently active Bitcoin Up/Down 15-minute market",
    )
    parser.add_argument(
        "--btc-15m-loop",
        action="store_true",
        help="Continuously paper-trade one baseline position in every Bitcoin 15-minute window",
    )
    parser.add_argument("--demo", action="store_true", help="Use bundled synthetic markets instead of the live API")
    args = parser.parse_args()

    if args.report:
        render_report(console)
        return

    if args.btc_15m_loop:
        run_btc_15m_loop(provider=args.provider)
        return

    run(
        max_research=1 if args.btc_15m else args.research,
        execute_paper=args.paper,
        baseline_paper=args.paper_baseline,
        demo=args.demo,
        provider=args.provider,
        btc_15m=args.btc_15m,
    )


if __name__ == "__main__":
    cli()
