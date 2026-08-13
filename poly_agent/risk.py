from __future__ import annotations

from .config import SETTINGS, Settings
from .models import Market, ProbabilityEstimate, TradeDecision


def _best_side(market: Market, estimate: ProbabilityEstimate) -> tuple[str, float, float, float]:
    yes_edge = estimate.fair_yes_probability - market.yes_price
    fair_no = 1 - estimate.fair_yes_probability
    no_edge = fair_no - market.no_price

    if yes_edge >= no_edge:
        return "YES", yes_edge, market.yes_price, estimate.fair_yes_probability
    return "NO", no_edge, market.no_price, fair_no


def _labels(market: Market) -> dict[str, str]:
    return {
        "positive_label": market.positive_label,
        "negative_label": market.negative_label,
    }


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
            **_labels(market),
        )

    side, edge, price, fair = _best_side(market, estimate)

    if edge < s.min_edge:
        side = "PASS"

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
        **_labels(market),
    )


def decide_baseline_trade(
    market: Market,
    estimate: ProbabilityEstimate,
    bankroll: float,
    s: Settings = SETTINGS,
) -> TradeDecision:
    """Always take the model's relatively better side with a small fixed paper stake."""
    side, edge, price, fair = _best_side(market, estimate)
    if price <= 0 or price >= 1:
        return TradeDecision(
            market_id=market.id,
            question=market.question,
            side="PASS",
            market_price=price,
            fair_probability=fair,
            edge=edge,
            confidence=estimate.confidence,
            stake=0,
            rationale="Invalid market price for baseline paper trade.",
            **_labels(market),
        )

    baseline_pct = max(0.0, min(s.baseline_position_pct, s.max_position_pct))
    stake = round(bankroll * baseline_pct, 2)
    return TradeDecision(
        market_id=market.id,
        question=market.question,
        side=side,
        market_price=price,
        fair_probability=fair,
        edge=edge,
        confidence=estimate.confidence,
        stake=stake,
        rationale=(
            "Baseline paper sample: fixed simulated stake; normal confidence and edge "
            "gates intentionally bypassed."
        ),
        **_labels(market),
    )
