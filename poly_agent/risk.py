from __future__ import annotations

from .config import SETTINGS, Settings
from .models import Market, ProbabilityEstimate, TradeDecision


def decide_trade(
    market: Market,
    estimate: ProbabilityEstimate,
    bankroll: float,
    s: Settings = SETTINGS,
) -> TradeDecision:
    yes_edge = estimate.fair_yes_probability - market.yes_price
    fair_no = 1 - estimate.fair_yes_probability
    no_edge = fair_no - market.no_price

    if estimate.confidence < s.min_confidence:
        return TradeDecision(
            market_id=market.id,
            question=market.question,
            side="PASS",
            market_price=market.yes_price,
            fair_probability=estimate.fair_yes_probability,
            edge=max(yes_edge, no_edge),
            confidence=estimate.confidence,
            stake=0,
            rationale="Confidence below minimum threshold.",
        )

    if yes_edge >= no_edge:
        side = "YES"
        edge = yes_edge
        price = market.yes_price
        fair = estimate.fair_yes_probability
    else:
        side = "NO"
        edge = no_edge
        price = market.no_price
        fair = fair_no

    if edge < s.min_edge:
        side = "PASS"

    # Fractional Kelly, then capped hard by portfolio policy.
    # Binary contract: profit per $1 payout share is 1-price.
    if side == "PASS" or price <= 0 or price >= 1:
        stake = 0.0
    else:
        b = (1 - price) / price
        q = 1 - fair
        kelly = max((b * fair - q) / b, 0)
        stake = bankroll * min(0.25 * kelly, s.max_position_pct)

    return TradeDecision(
        market_id=market.id,
        question=market.question,
        side=side,
        market_price=price,
        fair_probability=fair,
        edge=edge,
        confidence=estimate.confidence,
        stake=round(stake, 2),
        rationale=(
            f"Fair {side} probability exceeds market price by {edge:.1%}; "
            f"confidence {estimate.confidence:.0%}."
            if side != "PASS"
            else f"Best estimated edge {edge:.1%} is below the required threshold or confidence gate."
        ),
    )
