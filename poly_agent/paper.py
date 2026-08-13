from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import PaperPosition, TradeDecision

LEDGER = Path("data/paper_trades.jsonl")


def load_positions() -> list[PaperPosition]:
    if not LEDGER.exists():
        return []

    positions: list[PaperPosition] = []
    with LEDGER.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            positions.append(PaperPosition.model_validate(json.loads(line)))
    return positions


def already_traded_market(market_id: str) -> bool:
    return any(position.market_id == market_id for position in load_positions())


def record(decision: TradeDecision, *, allow_duplicate: bool = False) -> PaperPosition | None:
    if decision.side == "PASS" or decision.stake <= 0:
        return None

    if not allow_duplicate and already_traded_market(decision.market_id):
        return None

    shares = decision.stake / decision.market_price
    position = PaperPosition(
        timestamp=datetime.now(timezone.utc),
        market_id=decision.market_id,
        question=decision.question,
        side=decision.side,
        entry_price=decision.market_price,
        fair_probability=decision.fair_probability,
        confidence=decision.confidence,
        stake=decision.stake,
        shares=shares,
        estimated_edge=decision.edge,
    )
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(position.model_dump(mode="json")) + "\n")
    return position
