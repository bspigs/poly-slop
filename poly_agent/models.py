from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class Market(BaseModel):
    id: str
    question: str
    slug: str | None = None
    end_date: datetime | None = None
    yes_price: float = Field(ge=0, le=1)
    no_price: float = Field(ge=0, le=1)
    positive_label: str = "YES"
    negative_label: str = "NO"
    positive_token_id: str | None = None
    negative_token_id: str | None = None
    liquidity: float = 0
    volume: float = 0
    active: bool = True
    closed: bool = False
    description: str = ""


class ProbabilityEstimate(BaseModel):
    fair_yes_probability: float = Field(ge=0.01, le=0.99)
    confidence: float = Field(ge=0, le=1)
    thesis: str
    strongest_yes_evidence: list[str]
    strongest_no_evidence: list[str]
    key_uncertainties: list[str]
    resolution_risk: str


class TradeDecision(BaseModel):
    market_id: str
    question: str
    side: Literal["YES", "NO", "PASS"]
    market_price: float
    fair_probability: float
    edge: float
    confidence: float
    stake: float
    rationale: str
    positive_label: str = "YES"
    negative_label: str = "NO"


class PaperPosition(BaseModel):
    timestamp: datetime
    market_id: str
    question: str
    side: Literal["YES", "NO"]
    entry_price: float
    fair_probability: float
    confidence: float
    stake: float
    shares: float
    estimated_edge: float
    positive_label: str = "YES"
    negative_label: str = "NO"
