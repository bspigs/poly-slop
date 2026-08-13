from __future__ import annotations

import json
from typing import Literal

import requests

from .config import SETTINGS, Settings
from .models import Market, ProbabilityEstimate

Provider = Literal["auto", "openai", "ollama"]

SCHEMA = {
    "type": "object",
    "properties": {
        "fair_yes_probability": {"type": "number", "minimum": 0.01, "maximum": 0.99},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "thesis": {"type": "string"},
        "strongest_yes_evidence": {"type": "array", "items": {"type": "string"}},
        "strongest_no_evidence": {"type": "array", "items": {"type": "string"}},
        "key_uncertainties": {"type": "array", "items": {"type": "string"}},
        "resolution_risk": {"type": "string"},
    },
    "required": [
        "fair_yes_probability",
        "confidence",
        "thesis",
        "strongest_yes_evidence",
        "strongest_no_evidence",
        "key_uncertainties",
        "resolution_risk",
    ],
    "additionalProperties": False,
}

SYSTEM_COMMON = """You are a skeptical prediction-market research analyst.
Estimate the probability that the specified market resolves YES.
Do not merely anchor on the market price. Focus on base rates, timing, the exact
resolution wording, and strong arguments on both sides. Be conservative about
confidence. If the resolution criteria are ambiguous or evidence quality is weak,
reduce confidence. Return only the requested structured object."""

SYSTEM_OPENAI = SYSTEM_COMMON + "\nUse live web research when useful and prefer primary-source facts."
SYSTEM_OLLAMA = SYSTEM_COMMON + """
You do not have live web access. Use only the market information supplied below.
Never invent recent facts. If current external evidence would be necessary to make
a strong estimate, explicitly list that missing evidence and lower confidence."""


class ResearchProviderError(RuntimeError):
    pass


def resolve_provider(provider: str | None, s: Settings = SETTINGS) -> str:
    selected = (provider or s.research_provider or "auto").lower()
    if selected not in {"auto", "openai", "ollama"}:
        raise ResearchProviderError(f"Unknown research provider: {selected}")
    if selected == "auto":
        return "openai" if s.openai_api_key else "ollama"
    return selected


def _prompt(market: Market) -> str:
    return f"""Market question: {market.question}
Current YES price: {market.yes_price:.4f}
Current NO price: {market.no_price:.4f}
End date: {market.end_date}
Description / resolution context: {market.description[:5000]}

Independently estimate P(YES)."""


def _estimate_openai(market: Market, s: Settings) -> ProbabilityEstimate:
    if not s.openai_api_key:
        raise ResearchProviderError("OpenAI provider selected but OPENAI_API_KEY is not set.")

    from openai import OpenAI

    client = OpenAI(api_key=s.openai_api_key)
    response = client.responses.create(
        model=s.openai_model,
        reasoning={"effort": "medium"},
        tools=[{"type": "web_search"}],
        input=[
            {"role": "system", "content": SYSTEM_OPENAI},
            {"role": "user", "content": _prompt(market)},
        ],
        text={
            "format": {
                "type": "json_schema",
                "name": "probability_estimate",
                "schema": SCHEMA,
                "strict": True,
            }
        },
    )
    return ProbabilityEstimate.model_validate(json.loads(response.output_text))


def _estimate_ollama(market: Market, s: Settings) -> ProbabilityEstimate:
    url = f"{s.ollama_base_url.rstrip('/')}/api/chat"
    payload = {
        "model": s.ollama_model,
        "messages": [
            {"role": "system", "content": SYSTEM_OLLAMA},
            {"role": "user", "content": _prompt(market)},
        ],
        "stream": False,
        "format": SCHEMA,
        "options": {"temperature": 0.2},
    }
    try:
        response = requests.post(url, json=payload, timeout=s.research_timeout)
        response.raise_for_status()
        content = response.json()["message"]["content"]
        return ProbabilityEstimate.model_validate(json.loads(content))
    except requests.ConnectionError as exc:
        raise ResearchProviderError(
            "Cannot reach Ollama at " + s.ollama_base_url + ". Install/start Ollama, then run `ollama pull " + s.ollama_model + "`."
        ) from exc
    except requests.HTTPError as exc:
        detail = ""
        try:
            detail = response.json().get("error", "")
        except Exception:
            pass
        if "not found" in detail.lower():
            raise ResearchProviderError(
                f"Ollama model '{s.ollama_model}' is not installed. Run `ollama pull {s.ollama_model}`."
            ) from exc
        raise ResearchProviderError(f"Ollama request failed: {detail or exc}") from exc
    except (KeyError, ValueError, TypeError) as exc:
        raise ResearchProviderError("Ollama returned an invalid structured probability estimate.") from exc


def estimate_probability(
    market: Market,
    provider: str | None = None,
    s: Settings = SETTINGS,
) -> ProbabilityEstimate:
    selected = resolve_provider(provider, s)
    if selected == "openai":
        return _estimate_openai(market, s)
    return _estimate_ollama(market, s)
