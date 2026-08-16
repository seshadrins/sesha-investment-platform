from app.financial_analysis import _score_high


def test_quality_score_thresholds_and_missing_data():
    assert _score_high(21) == 100
    assert _score_high(16) == 80
    assert _score_high(12) == 60
    assert _score_high(7) == 40
    assert _score_high(2) == 20
    assert _score_high(None) is None
