# Codex instructions

This repository is a prediction-market research and paper-trading system.

Non-negotiable constraints:
- Keep execution paper-only unless a human explicitly designs and reviews a separate live-execution module.
- Never commit secrets, API keys, wallet keys, seed phrases, or private keys.
- Do not add code intended to bypass geoblocking or jurisdictional restrictions.
- Probability estimation and risk sizing must remain separate modules.
- Every change to risk logic needs tests.
- Support both OpenAI and local Ollama research providers; do not make an API key mandatory for scanner or Ollama mode.
- Local Ollama currently has no live web evidence in this repo. Never pretend local estimates include current web research; lower confidence when current evidence is missing.
- Prefer primary sources for factual research and preserve source URLs when feasible.
- Do not silently change MIN_EDGE, MIN_CONFIDENCE, or MAX_POSITION_PCT defaults.
- Avoid look-ahead bias in any future backtest code.
