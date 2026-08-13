from __future__ import annotations

import argparse

from rich.console import Console
from rich.table import Table

from .config import SETTINGS
from .paper import record
from .polymarket import fetch_markets, load_markets_from_file
from .research import estimate_probability
from .risk import decide_trade
from .scanner import rank_markets

console = Console()


def run(max_research: int = 5, execute_paper: bool = False, demo: bool = False) -> None:
    if demo:
        console.print("[bold]Loading synthetic demo markets…[/bold]")
        markets = load_markets_from_file("data/demo_markets.json")
    else:
        console.print("[bold]Fetching Polymarket markets…[/bold]")
        try:
            markets = fetch_markets(limit=max(100, SETTINGS.max_markets * 4))
        except Exception as exc:
            console.print(f"[red]Market fetch failed:[/red] {exc}")
            console.print("Run `python -m poly_agent.main --demo --research 0` to verify the local scanner without network access.")
            return
    ranked = rank_markets(markets)

    table = Table(title="Top Scanner Candidates")
    table.add_column("YES")
    table.add_column("Liquidity")
    table.add_column("Volume")
    table.add_column("Question")
    for m in ranked[:15]:
        table.add_row(f"{m.yes_price:.1%}", f"${m.liquidity:,.0f}", f"${m.volume:,.0f}", m.question)
    console.print(table)

    if not SETTINGS.openai_api_key:
        console.print("\n[yellow]No OPENAI_API_KEY found. Scanner works; AI research is skipped.[/yellow]")
        return

    bankroll = SETTINGS.starting_bankroll
    decisions = []
    for market in ranked[:max_research]:
        console.print(f"\n[bold cyan]Researching:[/bold cyan] {market.question}")
        estimate = estimate_probability(market)
        if estimate is None:
            continue
        decision = decide_trade(market, estimate, bankroll)
        decisions.append((market, estimate, decision))
        console.print(
            f"Market YES {market.yes_price:.1%} | fair YES {estimate.fair_yes_probability:.1%} | "
            f"confidence {estimate.confidence:.0%} | decision [bold]{decision.side}[/bold] | "
            f"edge {decision.edge:.1%} | stake ${decision.stake:,.2f}"
        )
        console.print(estimate.thesis)
        if execute_paper:
            position = record(decision)
            if position:
                console.print(f"[green]Paper position recorded: {position.side} ${position.stake:,.2f}[/green]")


def cli() -> None:
    parser = argparse.ArgumentParser(description="Polymarket research and paper-trading agent")
    parser.add_argument("--research", type=int, default=5, help="How many top markets to research with OpenAI")
    parser.add_argument("--paper", action="store_true", help="Record qualifying simulated trades")
    parser.add_argument("--demo", action="store_true", help="Use bundled synthetic markets instead of the live API")
    args = parser.parse_args()
    run(max_research=args.research, execute_paper=args.paper, demo=args.demo)


if __name__ == "__main__":
    cli()
