from __future__ import annotations

import argparse

from rich.console import Console
from rich.table import Table

from .config import SETTINGS
from .paper import record
from .polymarket import fetch_markets, load_markets_from_file
from .research import ResearchProviderError, estimate_probability, resolve_provider
from .risk import decide_baseline_trade, decide_trade
from .scanner import days_to_resolution, rank_markets

console = Console()


def run(
    max_research: int = 5,
    execute_paper: bool = False,
    baseline_paper: bool = False,
    demo: bool = False,
    provider: str | None = None,
) -> None:
    if demo:
        console.print("[bold]Loading synthetic demo markets…[/bold]")
        markets = load_markets_from_file("data/demo_markets.json")
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

    ranked = rank_markets(markets, enforce_resolution_window=not demo)

    table = Table(title="Top Scanner Candidates")
    table.add_column("YES")
    table.add_column("Resolves")
    table.add_column("Liquidity")
    table.add_column("Volume")
    table.add_column("Question")
    for m in ranked[:15]:
        days = days_to_resolution(m)
        resolves = "demo" if demo else (f"{days:.1f}d" if days is not None else "?")
        table.add_row(
            f"{m.yes_price:.1%}",
            resolves,
            f"${m.liquidity:,.0f}",
            f"${m.volume:,.0f}",
            m.question,
        )
    console.print(table)

    if not ranked:
        if not demo:
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
            f"records one ${fake_stake:,.2f} paper position per researched market."
        )
    elif execute_paper:
        console.print(
            "[bold yellow]CONSERVATIVE PAPER MODE[/bold yellow] - simulation only; "
            "records only trades that pass confidence/edge gates."
        )

    bankroll = SETTINGS.starting_bankroll

    for market in ranked[:max_research]:
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
        console.print(
            f"Market YES {market.yes_price:.1%} | fair YES {estimate.fair_yes_probability:.1%} | "
            f"confidence {estimate.confidence:.0%} | decision [bold]{decision.side}[/bold] | "
            f"edge {decision.edge:.1%} | stake ${decision.stake:,.2f}"
        )
        console.print(estimate.thesis)

        if execute_paper or baseline_paper:
            position = record(decision)
            if position:
                console.print(
                    f"[green]PAPER TRADE EXECUTED: {position.side} "
                    f"${position.stake:,.2f} at {position.entry_price:.1%}[/green]"
                )
            else:
                console.print("[yellow]No paper trade recorded for this market.[/yellow]")


def cli() -> None:
    parser = argparse.ArgumentParser(description="Polymarket research and paper-trading agent")
    parser.add_argument("--research", type=int, default=5, help="How many top markets to research")
    parser.add_argument("--provider", choices=["auto", "openai", "ollama"], default=SETTINGS.research_provider)
    parser.add_argument("--paper", action="store_true", help="Record qualifying simulated trades")
    parser.add_argument(
        "--paper-baseline",
        action="store_true",
        help="Record a small fixed simulated trade for every researched market, bypassing normal confidence/edge gates",
    )
    parser.add_argument("--demo", action="store_true", help="Use bundled synthetic markets instead of the live API")
    args = parser.parse_args()
    run(
        max_research=args.research,
        execute_paper=args.paper,
        baseline_paper=args.paper_baseline,
        demo=args.demo,
        provider=args.provider,
    )


if __name__ == "__main__":
    cli()
