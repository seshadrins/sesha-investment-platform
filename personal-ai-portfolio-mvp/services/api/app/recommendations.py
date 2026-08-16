from datetime import date

from .config import settings
from .models import ThesisStatus


def recommend(
    *,
    weight: float,
    return_pct: float | None,
    holding_days: int,
    thesis_status: ThesisStatus | None,
    has_price: bool,
) -> tuple[str, list[str]]:
    reasons: list[str] = []

    if not has_price:
        return "REVIEW", ["Current price is missing. Enter or import a recent price."]

    if thesis_status == ThesisStatus.INVALID:
        return "SELL", ["The investment thesis is marked invalid."]

    if weight >= settings.trim_position_weight:
        reasons.append(
            f"Position weight {weight:.1%} exceeds the trim threshold "
            f"of {settings.trim_position_weight:.1%}."
        )
        return "TRIM", reasons

    if return_pct is not None and return_pct <= settings.loss_review_threshold:
        reasons.append(
            f"Unrealised return {return_pct:.1%} crossed the loss-review threshold."
        )
        if thesis_status == ThesisStatus.WATCH:
            reasons.append("The thesis is already on watch.")
            return "SELL", reasons
        return "REVIEW", reasons

    if (
        return_pct is not None
        and return_pct >= settings.profit_review_threshold
        and weight >= settings.max_position_weight
    ):
        return "TRIM", [
            f"Return is {return_pct:.1%} and position weight is {weight:.1%}; "
            "consider partial profit realisation and rebalancing."
        ]

    if thesis_status == ThesisStatus.WATCH:
        return "REVIEW", ["The thesis is on watch and should be reassessed before adding."]

    if weight < settings.max_position_weight * 0.50 and (return_pct or 0) > -0.05:
        reasons.append("Position is well below the configured maximum allocation.")
        reasons.append("No hard thesis or portfolio risk gate is active.")
        return "ADD", reasons

    reasons.append("No concentration, loss-control or thesis-break rule requires action.")
    if holding_days < settings.preferred_hold_days:
        reasons.append(
            f"Preferred holding period has {settings.preferred_hold_days - holding_days} days remaining."
        )
    return "HOLD", reasons
