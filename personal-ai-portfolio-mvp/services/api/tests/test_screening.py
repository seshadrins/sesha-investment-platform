import httpx
import pytest

from app.screening import (
    ScreeningSourceError,
    fetch_universe_constituents,
    load_screening_universes,
    parse_constituent_csv,
    quarter_start,
    select_balanced_memberships,
)
from app.recommendations import recommend_prospective
from app.investor_following import classify_investor_signal, load_investor_config
from decimal import Decimal
from datetime import date
from types import SimpleNamespace


CSV = b"Company Name,Industry,Symbol,Series,ISIN Code\nABC Ltd,Technology,ABC,EQ,INE000A00000\n"


def test_nifty500_config_has_all_cap_segments():
    universe = load_screening_universes()[0]
    assert universe["id"] == "nifty500"
    assert {source["cap_segment"] for source in universe["sources"]} == {"LARGE", "MID", "SMALL"}
    assert universe["shortlist_recommendations"] == ["STRONG_BUY", "BUY"]


def test_constituent_parser_preserves_cap_segment():
    rows = parse_constituent_csv(CSV, "MID", "https://example.test/mid.csv")
    assert rows == [{
        "exchange": "NSE", "symbol": "ABC", "company_name": "ABC Ltd",
        "isin": "INE000A00000", "industry": "Technology", "cap_segment": "MID",
        "source_url": "https://example.test/mid.csv",
    }]


def test_constituent_parser_rejects_unexpected_file():
    with pytest.raises(ScreeningSourceError, match="expected columns"):
        parse_constituent_csv(b"wrong,columns\n1,2\n", "LARGE", "https://example.test")


def test_universe_fetch_rejects_overlap_between_segments():
    config = {"sources": [
        {"name": "Large", "cap_segment": "LARGE", "url": "https://example.test/large"},
        {"name": "Mid", "cap_segment": "MID", "url": "https://example.test/mid"},
    ]}
    client = httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=CSV)
    ))
    with pytest.raises(ScreeningSourceError, match="Duplicate constituent"):
        fetch_universe_constituents(config, client)


def test_strong_buy_requires_quality_styles_and_supportive_valuation():
    analysis = {
        "status": "READY", "overall_score": 84, "governance_flags": [],
        "valuation": {
            "PE": {"current": 20, "sector": 24},
            "PB": {"current": 4, "sector": 5},
        },
    }
    styles = [
        {"applicable": True, "matches": True},
        {"applicable": True, "matches": True},
        {"applicable": True, "matches": False},
    ]
    recommendation, reasons = recommend_prospective(analysis, styles)
    assert recommendation == "STRONG_BUY"
    assert any("2 configured investor styles" in reason for reason in reasons)


def test_high_governance_flag_blocks_prospective_promotion():
    recommendation, _ = recommend_prospective({
        "status": "READY", "overall_score": 95,
        "governance_flags": [{"severity": "HIGH", "message": "Material warning."}],
        "valuation": {},
    }, [{"applicable": True, "matches": True}] * 3)
    assert recommendation == "AVOID"


def test_followed_investor_config_is_versioned_and_has_aliases():
    config = load_investor_config()
    assert config["version"] >= 2
    assert len(config["investors"]) >= 15
    assert all(profile["aliases"] for profile in config["investors"])


def test_investor_activity_signals_are_materiality_aware():
    threshold = Decimal("0.10")
    assert classify_investor_signal(Decimal("1.2"), None, threshold)[0] == "NEW_DISCLOSURE"
    assert classify_investor_signal(Decimal("1.4"), Decimal("1.2"), threshold)[0] == "INCREASED"
    assert classify_investor_signal(Decimal("1.35"), Decimal("1.4"), threshold)[0] == "UNCHANGED"
    assert classify_investor_signal(Decimal("0"), Decimal("1.35"), threshold)[0] == "EXIT_REPORTED"


def test_quarter_start_identifies_screening_cycle():
    assert quarter_start(date(2026, 1, 1)) == date(2026, 1, 1)
    assert quarter_start(date(2026, 8, 16)) == date(2026, 7, 1)
    assert quarter_start(date(2026, 12, 31)) == date(2026, 10, 1)


def test_balanced_batch_rotates_across_cap_segments():
    memberships = [
        SimpleNamespace(cap_segment="LARGE", instrument_id=1),
        SimpleNamespace(cap_segment="LARGE", instrument_id=2),
        SimpleNamespace(cap_segment="MID", instrument_id=3),
        SimpleNamespace(cap_segment="SMALL", instrument_id=4),
        SimpleNamespace(cap_segment="SMALL", instrument_id=5),
    ]
    selected = select_balanced_memberships(memberships, 4)
    assert [item.instrument_id for item in selected] == [1, 3, 4, 2]
