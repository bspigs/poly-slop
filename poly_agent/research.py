from __future__ import annotations

import json

from .config import SETTINGS, Settings
from .models import Market, ProbabilityEstimate

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

SYSTEM = """You are a skeptical prediction-market research analyst.
Estimate the probability that the specified market resolves YES.
Use live web research when useful. Do not merely anchor on the market price.
Focus on base rates, primary-source facts, timing, the exact resolution wording,
and strong arguments on both sides. Be conservative about confidence.
If the resolution criteria are ambiguous or evidence quality is weak, reduce confidence.
Return only the requested structured object."""


def estimate_probability(market: Market, s: Settings = SETTINGS) -> ProbabilityEstimate | None:
    if not s.openai_api_key:
        return None

    from openai import OpenAI

    client = OpenAI(api_key=s.openai_api_key)
    prompt = f"""Market question: {market.question}
Current YES price: {market.yes_price:.4f}
Current NO price: {market.no_price:.4f}
End date: {market.end_date}
Description / resolution context: {market.description[:5000]}

Research the event and independently estimate P(YES)."""

    response = client.responses.create(
        model=s.openai_model,
        reasoning={"effort": "medium"},
        tools=[{"type": "web_search"}],
        input=[
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt},
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
