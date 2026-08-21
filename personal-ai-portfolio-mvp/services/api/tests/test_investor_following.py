from datetime import date
from decimal import Decimal
import json

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app.disclosure_pipeline import (
    ExchangeDisclosureClient,
    DisclosurePipelineError,
    ExtractedObservation,
    _process_document,
    _upsert_disclosure,
    decide_alias_review,
    extract_with_llm,
    extract_xbrl_observations,
    match_investor_alias,
    normalize_investor_name,
    run_disclosure_ingestion,
)
from app.investor_following import build_investor_signals, import_investor_disclosures
from app.models import (
    AppNotification,
    DisclosureDocument,
    DisclosureSourceMapping,
    Instrument,
    InvestorAliasReview,
    InvestorDisclosure,
)


def db_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_disclosure_import_and_increased_signal():
    db = db_session()
    content = (
        "investor_id,exchange,symbol,company_name,isin,report_date,filed_on,"
        "ownership_pct,shares,source_url,source_type\n"
        "vijay_kedia,NSE,ABC,ABC Ltd,INE000A00000,2025-12-31,2026-01-15,"
        "1.10,110000,https://www.nseindia.com/example-1,EXCHANGE_FILING\n"
        "vijay_kedia,NSE,ABC,ABC Ltd,INE000A00000,2026-03-31,2026-04-15,"
        "1.35,135000,https://www.nseindia.com/example-2,EXCHANGE_FILING\n"
    ).encode()
    imported = import_investor_disclosures(db, content)
    assert imported["imported"] == 2
    result = build_investor_signals(db)
    assert result["activity"][0]["signal"] == "INCREASED"
    assert result["activity"][0]["change_percentage_points"] == .25
    assert result["rows"][0]["investors"]["vijay_kedia"]["ownership_pct"] == 1.35


XBRL = b"""<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml"
      xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
      xmlns:shp="http://www.bseindia.com/xbrl/shp/2025-10-31/in-bse-shp">
  <body>
    <ix:nonNumeric name="shp:NameOfTheShareholder" contextRef="D_holder-1">VIJAY KEDIA</ix:nonNumeric>
    <ix:nonFraction name="shp:NumberOfShares" contextRef="holder-1">125000</ix:nonFraction>
    <ix:nonFraction name="shp:ShareholdingPercentage" contextRef="holder-1" scale="-2">1.25</ix:nonFraction>
  </body>
</html>"""


def test_deterministic_inline_xbrl_extraction():
    observations = extract_xbrl_observations(XBRL)
    assert len(observations) == 1
    assert observations[0].shareholder_name == "VIJAY KEDIA"
    assert observations[0].ownership_pct == 1.25
    assert observations[0].shares == 125000


def test_deterministic_plain_nse_xbrl_extraction_converts_ratio_to_percentage():
    content = b"""<?xml version="1.0"?>
    <xbrl xmlns="http://www.xbrl.org/2003/instance"
          xmlns:shp="http://www.nseindia.com/xbrl/shp">
      <shp:NameOfTheShareholder contextRef="D_holder-1">VIJAY KEDIA</shp:NameOfTheShareholder>
      <shp:NumberOfShares contextRef="holder-1" unitRef="shares">125000</shp:NumberOfShares>
      <shp:ShareholdingAsAPercentageOfTotalNumberOfShares
          contextRef="holder-1" unitRef="pure">0.0125</shp:ShareholdingAsAPercentageOfTotalNumberOfShares>
    </xbrl>"""
    observations = extract_xbrl_observations(content)
    assert len(observations) == 1
    assert observations[0].ownership_pct == 1.25
    assert observations[0].shares == 125000


def test_exact_alias_is_automatic_and_so_is_an_unambiguous_fuzzy_match():
    # Regression for the "True or ambiguous" dead-code bug: with only one plausible
    # candidate, neither an exact nor a clean fuzzy match should be forced into review.
    aliases = {normalize_investor_name("Vijay Kedia"): {"vijay_kedia"}}
    assert match_investor_alias("VIJAY KEDIA", aliases) == ("vijay_kedia", 1.0, False)
    investor_id, confidence, review = match_investor_alias(
        "VIJAY KEDIYA", aliases, threshold=.80
    )
    assert investor_id == "vijay_kedia"
    assert confidence > .80
    assert review is False


def test_ambiguous_fuzzy_match_between_two_close_candidates_requires_review():
    # Two candidates score identically against the observed name, so neither is
    # trustworthy enough to auto-apply.
    aliases = {
        normalize_investor_name("Vijay Kedia"): {"vijay_kedia"},
        normalize_investor_name("Vijay Kedib"): {"vijay_kedib_other"},
    }
    investor_id, confidence, review = match_investor_alias(
        "VIJAY KEDIC", aliases, threshold=.60
    )
    assert investor_id in {"vijay_kedia", "vijay_kedib_other"}
    assert review is True


def test_llm_sourced_observation_always_requires_review_even_with_exact_alias(monkeypatch):
    import app.disclosure_pipeline as dp

    db = db_session()
    instrument = Instrument(
        exchange="NSE", symbol="LLMTST", company_name="LLM Test Ltd", isin="INE000A00098"
    )
    db.add(instrument)
    db.flush()
    document = DisclosureDocument(
        mapping_id=1, instrument_id=instrument.id, exchange="NSE",
        report_date=date(2026, 3, 31), source_url="https://www.nseindia.com/llm-test.xml",
        content=b"unstructured filing text with no parseable XBRL",
    )
    db.add(document)
    db.commit()

    monkeypatch.setattr(dp, "extract_xbrl_observations", lambda content: [])
    monkeypatch.setattr(dp, "extract_with_llm", lambda content: (
        [ExtractedObservation(
            shareholder_name="Vijay Kedia", ownership_pct=Decimal("1.25"), shares=Decimal("125000")
        )],
        "OLLAMA:llama3", [],
    ))

    result = _process_document(db, document)

    assert result["parser"] == "OLLAMA:llama3"
    assert result["matched"] == 0
    assert result["reviews"] == 1
    review = db.scalar(select(InvestorAliasReview))
    assert review is not None
    assert review.proposed_investor_id == "vijay_kedia"
    assert float(review.confidence) == 1.0
    assert db.scalar(select(InvestorDisclosure)) is None


def test_near_miss_below_floor_is_recorded_but_not_an_unread_alert(monkeypatch):
    import app.disclosure_pipeline as dp

    db = db_session()
    instrument = Instrument(
        exchange="NSE", symbol="NEARM1", company_name="Near Miss One Ltd", isin="INE000A00099"
    )
    db.add(instrument)
    db.flush()
    document = DisclosureDocument(
        mapping_id=1, instrument_id=instrument.id, exchange="NSE",
        report_date=date(2026, 3, 31), source_url="https://www.nseindia.com/near-miss-low.xml",
        content=b"filing text",
    )
    db.add(document)
    db.commit()

    monkeypatch.setattr(dp, "extract_xbrl_observations", lambda content: [
        ExtractedObservation(shareholder_name="S Gopalakrishnan", ownership_pct=Decimal("0.1"))
    ])
    monkeypatch.setattr(
        dp, "match_investor_alias", lambda name, aliases, threshold=None: (None, 0.32, False)
    )

    result = _process_document(db, document)

    assert result["matched"] == 0
    notification = db.scalar(select(AppNotification).where(AppNotification.category == "ALIAS_REVIEW"))
    assert notification is not None
    assert notification.read_at is not None


def test_near_miss_within_reviewable_range_still_creates_unread_alert(monkeypatch):
    import app.disclosure_pipeline as dp

    db = db_session()
    instrument = Instrument(
        exchange="NSE", symbol="NEARM2", company_name="Near Miss Two Ltd", isin="INE000A00096"
    )
    db.add(instrument)
    db.flush()
    document = DisclosureDocument(
        mapping_id=1, instrument_id=instrument.id, exchange="NSE",
        report_date=date(2026, 3, 31), source_url="https://www.nseindia.com/near-miss-high.xml",
        content=b"filing text",
    )
    db.add(document)
    db.commit()

    monkeypatch.setattr(dp, "extract_xbrl_observations", lambda content: [
        ExtractedObservation(
            shareholder_name="Radhakishan Shivkishan Damani HUF", ownership_pct=Decimal("0.5")
        )
    ])
    monkeypatch.setattr(
        dp, "match_investor_alias", lambda name, aliases, threshold=None: (None, 0.70, False)
    )

    result = _process_document(db, document)

    assert result["matched"] == 0
    notification = db.scalar(select(AppNotification).where(AppNotification.category == "ALIAS_REVIEW"))
    assert notification is not None
    assert notification.read_at is None


def test_upsert_disclosure_recovers_from_concurrent_insert_race(tmp_path):
    db_path = tmp_path / "race.db"
    engine = create_engine(f"sqlite+pysqlite:///{db_path}")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    setup_db = SessionLocal()
    instrument = Instrument(
        exchange="NSE", symbol="RACE", company_name="Race Ltd", isin="INE000A00097"
    )
    setup_db.add(instrument)
    setup_db.commit()
    document = DisclosureDocument(
        mapping_id=1, instrument_id=instrument.id, exchange="NSE",
        report_date=date(2026, 6, 30), source_url="https://www.nseindia.com/race.xml",
    )
    setup_db.add(document)
    setup_db.commit()
    instrument_id, document_id = instrument.id, document.id
    setup_db.close()

    db_a = SessionLocal()
    document_a = db_a.get(DisclosureDocument, document_id)
    observation = ExtractedObservation(shareholder_name="Race Investor", ownership_pct=Decimal("5"))

    real_scalar = db_a.scalar
    calls = {"n": 0}

    def scalar_missing_once_then_real(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # Simulate an overlapping scheduled/forced run committing the same
            # (investor, instrument, period) row in its own independent transaction,
            # landing between this call's own existence check and its insert.
            db_b = SessionLocal()
            db_b.add(InvestorDisclosure(
                investor_id="RACE_INV", instrument_id=instrument_id,
                report_date=date(2026, 6, 30), ownership_pct=Decimal("3"),
                source_url="https://race/other-process", source_type="XBRL",
            ))
            db_b.commit()
            db_b.close()
            return None
        return real_scalar(*args, **kwargs)

    db_a.scalar = scalar_missing_once_then_real
    action = _upsert_disclosure(db_a, document_a, observation, "RACE_INV", "XBRL")
    db_a.commit()

    assert action == "UPDATED"
    stored = real_scalar(select(InvestorDisclosure).where(
        InvestorDisclosure.investor_id == "RACE_INV",
        InvestorDisclosure.instrument_id == instrument_id,
    ))
    assert stored is not None
    assert stored.ownership_pct == Decimal("5")
    assert stored.source_url == document_a.source_url


def test_bse_discovery_ingestion_creates_disclosure_and_notification(monkeypatch):
    db = db_session()
    instrument = Instrument(
        exchange="BSE", symbol="500001", company_name="Example Ltd", isin="INE000A00000"
    )
    db.add(instrument)
    db.flush()
    mapping = DisclosureSourceMapping(
        instrument_id=instrument.id, exchange="BSE", source_code="500001"
    )
    db.add(mapping)
    db.commit()

    def handler(request: httpx.Request) -> httpx.Response:
        if "Corp_Shareholding_ng" in str(request.url):
            return httpx.Response(200, json={"Table": [{
                "EndDate": "2026-03-31T00:00:00", "D": "2026-04-15T12:00:00",
                "IsXBRL": 1,
                "XBRLAttachment": "/XBRLFILES/SHPXBRLDataXML/500001_test_SP.html",
            }]})
        return httpx.Response(200, content=XBRL, headers={"content-type": "text/html"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    exchange = ExchangeDisclosureClient(http, interval_seconds=0)
    result = run_disclosure_ingestion(
        db, date(2026, 3, 31), force=True, mapping_ids=[mapping.id], client=exchange
    )
    assert result["status"] == "SUCCESS"
    assert result["matched"] == 1
    disclosure = db.scalar(select(InvestorDisclosure))
    assert disclosure.investor_id == "vijay_kedia"
    assert disclosure.ownership_pct == 1.25
    assert db.scalar(select(AppNotification)).category == "INVESTOR_DISCLOSURE"


def test_bse_unexpected_empty_shape_is_retryable_failure():
    client = ExchangeDisclosureClient(
        httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={})
        )),
        interval_seconds=0,
    )
    mapping = type("Mapping", (), {"exchange": "BSE", "source_code": "500001"})()
    with pytest.raises(DisclosurePipelineError, match="throttled/source failure"):
        client.discover(mapping, date(2026, 3, 31))


def test_fuzzy_exchange_name_enters_review_then_approval_creates_evidence(monkeypatch):
    # Two configured investors with near-identical aliases so the observed name in the
    # filing is genuinely ambiguous between them (not auto-applied to either), regardless
    # of confidence — this exercises the review->approval path on its own merits rather
    # than relying on the now-fixed "always require review" dead-code bug.
    monkeypatch.setattr("app.disclosure_pipeline.load_investor_config", lambda: {
        "version": 1,
        "investors": [
            {"id": "vijay_kedia", "name": "Vijay Kedia", "aliases": ["VIJAY KEDIA"], "enabled": True},
            {"id": "close_match_other", "name": "Close Match Other",
             "aliases": ["VIJAY KEDIB"], "enabled": True},
        ],
    })
    db = db_session()
    instrument = Instrument(
        exchange="BSE", symbol="500002", company_name="Review Ltd", isin="INE000A00001"
    )
    db.add(instrument)
    db.flush()
    mapping = DisclosureSourceMapping(
        instrument_id=instrument.id, exchange="BSE", source_code="500002"
    )
    db.add(mapping)
    db.commit()
    fuzzy_xbrl = XBRL.replace(b"VIJAY KEDIA", b"VIJAY KEDIC")

    def handler(request: httpx.Request) -> httpx.Response:
        if "Corp_Shareholding_ng" in str(request.url):
            return httpx.Response(200, json={"Table": [{
                "EndDate": "2026-03-31", "D": "2026-04-15T12:00:00", "IsXBRL": 1,
                "XBRLAttachment": "/XBRLFILES/SHPXBRLDataXML/500002_test_SP.html",
            }]})
        return httpx.Response(200, content=fuzzy_xbrl)

    exchange = ExchangeDisclosureClient(
        httpx.Client(transport=httpx.MockTransport(handler)), interval_seconds=0
    )
    result = run_disclosure_ingestion(
        db, date(2026, 3, 31), force=True, mapping_ids=[mapping.id], client=exchange
    )
    assert result["matched"] == 0
    assert result["reviews_created"] == 1
    review = db.scalar(select(InvestorAliasReview))
    decide_alias_review(db, review.id, "APPROVED", "vijay_kedia", "Identity verified")
    assert db.scalar(select(InvestorDisclosure)).investor_id == "vijay_kedia"


def test_auto_llm_falls_back_from_ollama_to_pinned_openrouter(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "disclosure_llm_provider", "auto")
    monkeypatch.setattr(settings, "ollama_base_url", "http://ollama.test")
    monkeypatch.setattr(settings, "openrouter_api_key", "test-key")
    monkeypatch.setattr(settings, "openrouter_model", "openai/gpt-4o-mini")
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "ollama.test":
            return httpx.Response(503, text="offline")
        return httpx.Response(200, json={"choices": [{"message": {"content": (
            '{"observations":[{"shareholder_name":"VIJAY KEDIA",'
            '"ownership_pct":1.25,"shares":125000}]}'
        )}}]})

    observations, provider, errors = extract_with_llm(
        b"unstructured filing", httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert observations[0].ownership_pct == 1.25
    assert provider == "OPENROUTER:openai/gpt-4o-mini"
    assert errors and errors[0].startswith("ollama:")
    openrouter_request = requests[-1]
    payload = json.loads(openrouter_request.content)
    assert payload["provider"]["data_collection"] == "deny"
    assert payload["model"] == "openai/gpt-4o-mini"
