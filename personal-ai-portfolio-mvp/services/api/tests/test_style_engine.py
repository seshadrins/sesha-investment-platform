from datetime import date

from app.style_engine import _period_end, evaluate_style, load_styles


def test_style_configs_are_versioned_and_valid():
    styles = load_styles()
    assert {style["id"] for style in styles} == {
        "quality_compounder", "growth_at_quality", "capital_preservation"
    }
    assert all(style["version"] >= 1 and style["rules"] for style in styles)


def test_style_evaluation_reports_score_coverage_and_rules():
    style = {
        "id": "test", "name": "Test", "version": 1,
        "minimum_score": 60, "minimum_coverage": 50,
        "rules": [
            {"feature": "growth", "operator": "gte", "value": 10, "weight": 60},
            {"feature": "leverage", "operator": "lte", "value": .5, "weight": 40},
        ],
    }
    result = evaluate_style(style, {"growth": 12, "leverage": None})
    assert result["score"] == 100
    assert result["coverage"] == 60
    assert result["matches"] is True
    assert result["rules"][1]["passed"] is None


def test_reporting_period_end_is_calendar_accurate():
    assert _period_end("Mar 2025") == date(2025, 3, 31)
    assert _period_end("Feb 2024") == date(2024, 2, 29)
