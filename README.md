# Poly Slop

A live-data Polymarket research + paper-trading agent.

It pulls active markets, ranks liquid candidates, estimates fair `P(YES)`, applies deterministic risk gates, and can record simulated positions. It intentionally contains no wallet integration or live order-placement code.

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

Test the AI path against bundled synthetic markets:

```powershell
python -m poly_agent.main --demo --provider ollama --research 1
```

Run against live Polymarket market data:

```powershell
python -m poly_agent.main --provider ollama --research 5
```

Record qualifying simulated trades:

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
python -m poly_agent.main --provider openai --research 5
```

## Current risk gates

Defaults in `.env.example`:

- Minimum estimated edge: 8 percentage points
- Minimum confidence: 60%
- Maximum position: 1% of bankroll
- Position sizing: 1/4 Kelly, hard-capped by max position

These are starter safety values, not claims of optimal sizing.

## GitHub Actions

`.github/workflows/scan.yml` runs tests and a keyless market scan every 6 hours. Cloud AI research runs only when `OPENAI_API_KEY` is configured as a repository secret. A GitHub-hosted runner cannot use the Ollama server running on your home PC.

## Codex

`AGENTS.md` contains repo-level rules for Codex. Good next tasks include:

- Add SQLite storage for market snapshots and forecasts.
- Add resolved-market settlement and Brier-score calibration.
- Add a keyless public-source retrieval layer for Ollama with source timestamps and URLs.
- Add a dashboard for modeled edge, calibration, and paper P&L.

## Next milestones

1. Persistent market-snapshot database
2. Resolution ingestion and P&L settlement
3. Brier score + calibration curves
4. Source capture/audit trail
5. Duplicate/correlated-market exposure controls
6. Historical backtesting
7. Keyless web evidence for local Ollama

Only after those work should live execution even be evaluated.
