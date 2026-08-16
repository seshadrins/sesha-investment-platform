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
        return "STRONG_SELL", [
            "The investment thesis is marked invalid; the core reason for owning the stock no longer holds."
        ]

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
        return "BUY_MORE", reasons

    reasons.append("No concentration, loss-control or thesis-break rule requires action.")
    if holding_days < settings.preferred_hold_days:
        reasons.append(
            f"Preferred holding period has {settings.preferred_hold_days - holding_days} days remaining."
        )
    return "HOLD", reasons


def recommend_prospective(analysis: dict, styles: list[dict]) -> tuple[str, list[str]]:
    """Rate a prospective stock from stored evidence; never places or sizes an order."""
    if analysis.get("status") == "MISSING_DATA":
        return "REVIEW", ["Required financial evidence is missing: " + ", ".join(analysis.get("missing", []))]
    if analysis.get("status") == "SECTOR_SPECIFIC_REQUIRED":
        return "REVIEW", ["This financial-sector company requires bank/NBFC/insurance-specific rules."]

    flags = analysis.get("governance_flags", [])
    high_flags = [flag for flag in flags if flag.get("severity") == "HIGH"]
    if high_flags:
        return "AVOID", [flag["message"] for flag in high_flags]

    overall = analysis.get("overall_score")
    matches = [style for style in styles if style.get("applicable") and style.get("matches")]
    valuation = analysis.get("valuation", {})
    comparisons = [item for item in valuation.values()
                   if item.get("current") is not None and item.get("sector") not in (None, 0)]
    supportive = [item for item in comparisons if item["current"] <= item["sector"]]
    clearly_expensive = [item for item in comparisons if item["current"] >= item["sector"] * 1.25]
    valuation_supportive = bool(comparisons) and len(supportive) / len(comparisons) >= .5
    valuation_stretched = bool(comparisons) and len(clearly_expensive) / len(comparisons) >= .5

    if overall is not None and overall < 50:
        return "AVOID", [f"Financial-quality score is only {overall}/100."]
    if overall is not None and overall >= 80 and len(matches) >= 2 and valuation_supportive:
        return "STRONG_BUY", [
            f"Financial-quality score is {overall}/100.",
            f"Matches {len(matches)} configured investor styles.",
            "At least half of available valuation ratios are at or below sector values.",
            "No high-severity automated governance flag is active.",
        ]
    if overall is not None and overall >= 70 and matches and not valuation_stretched:
        reasons = [f"Financial-quality score is {overall}/100.",
                   f"Matches {len(matches)} configured investor style(s)."]
        reasons.append("Available valuation comparisons are not broadly 25% above sector values.")
        return "BUY", reasons

    reasons = []
    if overall is not None:
        reasons.append(f"Financial-quality score is {overall}/100.")
    if not matches:
        reasons.append("Does not currently match a configured investor style.")
    if valuation_stretched:
        reasons.append("Most available valuation ratios are at least 25% above sector values.")
    if not comparisons:
        reasons.append("Sector-relative valuation evidence is unavailable.")
    return "WATCH", reasons or ["More evidence is required before assigning a Buy rating."]
