# Poly Agent

A live-data **research + paper-trading** agent for Polymarket.

It does four things:
1. Pulls active markets from Polymarket's public Gamma API.
2. Filters/ranks liquid markets with non-extreme prices.
3. Optionally asks an OpenAI model with web search to independently estimate `P(YES)` in strict structured output.
4. Applies deterministic confidence, edge, fractional-Kelly, and max-position rules before recording a simulated trade.

## Why paper-only?

This starter intentionally contains **no wallet integration and no order-placement code**. The goal is to measure calibration and realized edge before considering any real-money execution, and to avoid creating code that bypasses platform/jurisdiction restrictions.

## Setup

```bash
python -m venv .venv
# Windows
.venv\\Scripts\\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
cp .env.example .env
```

Put your OpenAI API key into `.env` if you want AI research. Without it, the live market scanner still works.

## Run

Scanner only:

```bash
python -m poly_agent.main --research 0
```

Offline smoke test with bundled synthetic markets:

```bash
python -m poly_agent.main --demo --research 0
```

Research the top 5 candidates:

```bash
python -m poly_agent.main --research 5
```

Research + record qualifying simulated positions:

```bash
python -m poly_agent.main --research 5 --paper
```

Paper trades are appended to `data/paper_trades.jsonl` and excluded from git.

## Current risk gates

Defaults are in `.env.example`:
- Minimum estimated edge: 8 percentage points
- Minimum confidence: 60%
- Maximum position: 1% of bankroll
- Position sizing: 1/4 Kelly, hard-capped by max position

These are intentionally conservative starter values, not claims of optimal sizing.

## GitHub Actions

`.github/workflows/scan.yml` runs tests and a scan every 6 hours. Add `OPENAI_API_KEY` as a GitHub Actions repository secret if you want the AI research step.

The workflow currently **does not commit paper trades** because CI runners are ephemeral. For persistent autonomous logging, the next version should use SQLite/Postgres/Supabase or upload a run artifact.

## Codex

`AGENTS.md` gives Codex repository-level rules. Useful prompts include:

- `Add a calibration module that computes Brier score and reliability buckets from resolved paper trades.`
- `Add a SQLite store for market snapshots and model estimates without changing risk thresholds.`
- `Add a backtest command with strict timestamp cutoffs to prevent look-ahead bias.`
- `Add a dashboard showing estimated edge, confidence, paper P&L, and calibration.`

## Next milestones

1. Persistent market-snapshot database
2. Resolution ingestion and P&L settlement
3. Brier score + calibration curves
4. Source capture/audit trail for each estimate
5. Duplicate/correlated-market exposure controls
6. Notifications for new qualifying paper trades
7. Historical backtesting

Only after those work should live execution even be evaluated.
