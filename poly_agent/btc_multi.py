from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console

from .btc_feed import RobustCoinbaseSpotFeed, WindowOpenUnavailable
from .btc_scalper import (
    ScalpTrade,
    current_paper_equity,
    load_scalps,
    manage_scalp,
    recover_expired_open_scalps,
    scalp_metrics,
)
from .btc_signal import BtcSignal, BtcSignalEngine, manage_signal_scalp, open_signal_scalp
from .config import SETTINGS, Settings
from .polymarket import btc_15m_slug, fetch_current_btc_15m_market

V2_IDS = Path("data/btc_v2_trade_ids.jsonl")


def _open_trade_for_market(market_id: str) -> ScalpTrade | None:
    """Return the currently open scalp for a market, if one exists."""
    for trade in reversed(load_scalps()):
        if trade.market_id == market_id and trade.status == "OPEN":
            return trade
    return None


def _trade_count_for_market(market_id: str) -> int:
    return sum(trade.market_id == market_id for trade in load_scalps())


def _load_v2_ids() -> set[str]:
    if not V2_IDS.exists():
        return set()
    ids: set[str] = set()
    with V2_IDS.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                trade_id = str(payload.get("trade_id") or "")
                if trade_id:
                    ids.add(trade_id)
            except json.JSONDecodeError:
                continue
    return ids


def _record_v2_id(trade: ScalpTrade, signal: BtcSignal) -> None:
    V2_IDS.parent.mkdir(parents=True, exist_ok=True)
    if trade.trade_id in _load_v2_ids():
        return
    payload = {
        "trade_id": trade.trade_id,
        "market_id": trade.market_id,
        "side": trade.side,
        "entry_time": trade.entry_time.isoformat(),
        "spot": signal.spot_price,
        "window_open_spot": signal.window_open_price,
        "distance_bps": signal.distance_bps,
        "momentum_15s_bps": signal.momentum_15s_bps,
        "momentum_30s_bps": signal.momentum_30s_bps,
        "fair_side_probability": signal.fair_side_probability,
        "fee_adjusted_edge": signal.fee_adjusted_edge,
    }
    with V2_IDS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload) + "\n")


def _v2_metrics(s: Settings = SETTINGS) -> dict[str, float | int]:
    ids = _load_v2_ids()
    trades = [t for t in load_scalps() if t.trade_id in ids]
    closed = [t for t in trades if t.status == "CLOSED" and t.realized_pnl is not None]
    wins = [t for t in closed if (t.realized_pnl or 0) > 0]
    losses = [t for t in closed if (t.realized_pnl or 0) < 0]
    pnl = sum(t.realized_pnl or 0 for t in closed)
    fees = sum(t.entry_fee + t.exit_fee for t in closed)
    return {
        "trades": len(trades),
        "closed": len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "hit_rate": len(wins) / len(closed) if closed else 0.0,
        "net_pnl": pnl,
        "fees": fees,
        "standalone_equity": s.starting_bankroll + pnl,
        "total_ledger_equity": current_paper_equity(s),
    }


def render_v2_report(console: Console | None = None, s: Settings = SETTINGS) -> dict[str, float | int]:
    console = console or Console()
    recover_expired_open_scalps(console)
    metrics = _v2_metrics(s)
    console.print("\n[bold]POLY SLUDGE BTC V2 MICROSTRUCTURE SCOREBOARD[/bold]")
    console.print(
        f"Trades {metrics['trades']} | Closed {metrics['closed']} | "
        f"{metrics['wins']}W/{metrics['losses']}L | hit {float(metrics['hit_rate']):.1%}"
    )
    console.print(
        f"V2 net ${float(metrics['net_pnl']):+,.2f} | fees ${float(metrics['fees']):,.2f} | "
        f"V2 standalone equity ${float(metrics['standalone_equity']):,.2f} | "
        f"total ledger equity ${float(metrics['total_ledger_equity']):,.2f}"
    )
    return metrics


def _signal_text(signal: BtcSignal) -> str:
    m15 = "?" if signal.momentum_15s_bps is None else f"{signal.momentum_15s_bps:+.1f}bp"
    m30 = "?" if signal.momentum_30s_bps is None else f"{signal.momentum_30s_bps:+.1f}bp"
    ask = "-" if signal.selected_ask is None else f"{signal.selected_ask:.3f}"
    return (
        f"{signal.action} | BTC ${signal.spot_price:,.0f} vs open ${signal.window_open_price:,.0f} "
        f"({signal.distance_bps:+.1f}bp) | m15 {m15} | m30 {m30} | "
        f"P(up) {signal.fair_up_probability:.1%} | ask {ask} | "
        f"fee-adj edge {signal.fee_adjusted_edge:+.1%} | {signal.reason}"
    )


def run_multi_scalp_loop(
    provider: str | None = None,
    *,
    s: Settings = SETTINGS,
    console: Console | None = None,
) -> None:
    """BTC v2: wait for measurable microstructure edge, then paper scalp it."""
    del provider  # v2 deliberately does not let the LLM choose trade direction.
    console = console or Console()
    engine = BtcSignalEngine(s, feed=RobustCoinbaseSpotFeed())
    console.print(
        "[bold yellow]BTC 15M V2 MICROSTRUCTURE SCALPER - PAPER ONLY[/bold yellow] | "
        "Coinbase BTC spot + distance-to-window-open + realized vol + momentum + "
        "Polymarket spread/fee/depth gates. PASS is expected. Ctrl+C stops immediately."
    )

    current_market_id: str | None = None
    last_signal_line = ""
    last_signal_print = 0.0
    last_open_wait_print = 0.0

    try:
        while True:
            try:
                recover_expired_open_scalps(console)
                market = fetch_current_btc_15m_market()
                _, window_start, window_end = btc_15m_slug(datetime.now(timezone.utc))
                seconds_left = (window_end - datetime.now(timezone.utc)).total_seconds()

                if market.id != current_market_id:
                    current_market_id = market.id
                    last_signal_line = ""
                    console.print(
                        f"\n[bold]NEW BTC 15M WINDOW[/bold] | {market.question} | "
                        f"{max(seconds_left, 0):.0f}s remaining"
                    )

                existing = _open_trade_for_market(market.id)
                if existing is not None:
                    if existing.trade_id in _load_v2_ids():
                        result = manage_signal_scalp(
                            existing,
                            market,
                            window_start,
                            engine,
                            s=s,
                            console=console,
                        )
                    else:
                        console.print("[yellow]Finishing a legacy v1 paper position before v2 can enter.[/yellow]")
                        result = manage_scalp(existing, s=s, console=console)
                    if result.status == "CLOSED":
                        render_v2_report(console, s)
                        time.sleep(s.btc_signal_reentry_cooldown_seconds)
                    continue

                if seconds_left < s.btc_scalp_min_entry_seconds:
                    sleep_for = max(seconds_left + 2, 2)
                    console.print(
                        f"[dim]Entry cutoff reached ({seconds_left:.0f}s left). "
                        f"Waiting {sleep_for:.0f}s for next window…[/dim]"
                    )
                    time.sleep(sleep_for)
                    continue

                signal = engine.evaluate(market, window_start, window_end)
                line = _signal_text(signal)
                now_mono = time.monotonic()
                if line != last_signal_line or now_mono - last_signal_print >= 10:
                    style = "bold green" if signal.action != "PASS" else "dim"
                    console.print(f"[{style}]SIGNAL:[/{style}] {line}")
                    last_signal_line = line
                    last_signal_print = now_mono

                if signal.action == "PASS":
                    time.sleep(max(s.btc_signal_poll_seconds, 0.5))
                    continue

                trade = open_signal_scalp(market, signal, s=s, console=console)
                _record_v2_id(trade, signal)
                result = manage_signal_scalp(
                    trade,
                    market,
                    window_start,
                    engine,
                    s=s,
                    console=console,
                )
                if result.status == "CLOSED":
                    v2 = render_v2_report(console, s)
                    overall = scalp_metrics(load_scalps(), s)
                    console.print(
                        f"[dim]Control + v2 ledger: {overall['wins']}W/{overall['losses']}L | "
                        f"net ${float(overall['net_pnl']):+,.2f}. "
                        f"V2 alone: {v2['wins']}W/{v2['losses']}L | "
                        f"net ${float(v2['net_pnl']):+,.2f}.[/dim]"
                    )
                    time.sleep(s.btc_signal_reentry_cooldown_seconds)

            except KeyboardInterrupt:
                raise
            except WindowOpenUnavailable:
                now_mono = time.monotonic()
                if now_mono - last_open_wait_print >= 10:
                    console.print(
                        "[dim]Waiting for Coinbase to publish the exact BTC window-open candle…[/dim]"
                    )
                    last_open_wait_print = now_mono
                time.sleep(max(s.btc_signal_poll_seconds, 1.0))
            except Exception as exc:
                console.print(f"[yellow]V2 loop error:[/yellow] {exc}")
                time.sleep(2)

    except KeyboardInterrupt:
        console.print("\n[bold]BTC v2 loop stopped.[/bold]")
        render_v2_report(console, s)