from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import PaperPosition, TradeDecision

LEDGER = Path("data/paper_trades.jsonl")


def record(decision: TradeDecision) -> PaperPosition | None:
    if decision.side == "PASS" or decision.stake <= 0:
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
