# Poly Slop

A live-data Polymarket research + paper-trading agent.

It pulls active markets, ranks liquid near-term candidates, estimates fair `P(YES)`, records simulated positions, and can automatically check resolved markets to score the paper strategy. It intentionally contains no wallet integration or live order-placement code.

## Research providers

Poly Slop supports three modes:

- `--provider ollama`: fully local AI research; no API key required.
- `--provider openai`: OpenAI Responses API with web search; requires `OPENAI_API_KEY`.
- `--provider auto`: OpenAI when a key exists, otherwise local Ollama.

The local Ollama path does **not** currently have live web research. It only uses the market question, prices, end date, and Polymarket description. The prompt explicitly tells the local model not to invent recent facts and to lower confidence when current evidence is missing.

## Python setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

## Keyless local AI with Ollama

Install Ollama for Windows, then download the default model:

```powershell
ollama pull qwen3:8b
```

Ollama serves its local API at `http://localhost:11434` and local access does not require authentication.

## Short-term baseline paper experiment

The scanner defaults to markets resolving within the next 7 days.

To create a baseline sample that actually places simulated trades instead of passing on conservative confidence/edge gates:

```powershell
python -m poly_agent.main --provider ollama --research 5 --paper-baseline
```

Baseline mode uses a fixed small paper stake (default 0.25% of the fake bankroll, or $25 on a $10,000 paper bankroll) and records at most one baseline position per market.

Run it again later to add new short-term markets. Markets already present in the paper ledger are skipped.

## Paper scoreboard

Check every paper position against Polymarket and print resolved wins/losses and performance:

```powershell
python -m poly_agent.main --report
```

The report shows:

- total, resolved, pending, and failed lookups
- wins and losses
- hit rate
- realized paper P&L
- ROI on resolved stakes
- Brier score for probability calibration
- current paper equity

A position is treated as resolved only when Polymarket reports the market closed and the binary outcome prices have finalized to 1/0. Winning paper shares pay $1 each; losing paper shares pay $0.

Paper trades live in:

```text
data/paper_trades.jsonl
```

## Conservative paper mode

To record only trades that pass the normal risk gates:

```powershell
python -m poly_agent.main --provider ollama --research 5 --paper
```

## Scanner only

No model is needed:

```powershell
python -m poly_agent.main --research 0
```

## OpenAI mode

Put `OPENAI_API_KEY` in `.env`, then:

```powershell
python -m poly_agent.main --provider openai --research 5 --paper-baseline
```

## Current settings

Defaults in `.env.example`:

- Resolution window: 0 to 7 days
- Minimum estimated edge in conservative mode: 8 percentage points
- Minimum confidence in conservative mode: 60%
- Maximum position: 1% of bankroll
- Conservative sizing: 1/4 Kelly, hard-capped by max position
- Baseline paper position: 0.25% of bankroll

These are starter experiment settings, not claims of optimal sizing.

## GitHub Actions

`.github/workflows/scan.yml` runs tests and a keyless market scan every 6 hours. Cloud AI research runs only when `OPENAI_API_KEY` is configured as a repository secret. A GitHub-hosted runner cannot use the Ollama server running on your home PC.

## Codex

`AGENTS.md` contains repo-level rules for Codex. Good next tasks include:

- Add SQLite storage for market snapshots and forecasts.
- Add a keyless public-source retrieval layer for Ollama with source timestamps and URLs.
- Add calibration buckets/reliability curves after enough markets resolve.
- Add a dashboard for modeled edge, calibration, and paper P&L.

## Next milestones

1. Persistent SQLite market-snapshot database
2. Calibration buckets/reliability curves
3. Source capture/audit trail
4. Duplicate/correlated-market exposure controls
5. Historical backtesting
6. Keyless web evidence for local Ollama

Only after the paper system has a meaningful resolved sample should live execution even be evaluated.
