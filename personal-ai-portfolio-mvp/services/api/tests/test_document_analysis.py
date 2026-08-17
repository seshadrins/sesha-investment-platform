from datetime import date

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import document_analysis
from app.database import Base
from app.document_analysis import analysis_out, analyze_document, store_document
from app.models import Instrument, ResearchDocumentSection


def db_session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_text_document_is_sectioned_deduplicated_and_grounded(monkeypatch):
    db = db_session()
    instrument = Instrument(exchange="NSE", symbol="ABC", company_name="ABC Ltd")
    db.add(instrument); db.commit()
    content = b"Management expects a new plant to open.\n\nCustomer concentration is a material risk."
    document = store_document(db, instrument_id=instrument.id, document_type="TRANSCRIPT",
        title="Q1 call", report_date=date(2026, 6, 30), source_url="https://example.com/call",
        filename="call.txt", content_type="text/plain", content=content)
    duplicate = store_document(db, instrument_id=instrument.id, document_type="TRANSCRIPT",
        title="Duplicate", report_date=date(2026, 6, 30), source_url=None,
        filename="call.txt", content_type="text/plain", content=content)
    assert duplicate.id == document.id
    section = db.scalar(select(ResearchDocumentSection).where(
        ResearchDocumentSection.document_id == document.id))

    monkeypatch.setattr(document_analysis, "_invoke", lambda prompt: ({
        "summary": "Management discussed expansion and concentration risk.",
        "summary_section_ids": [section.id],
        "catalysts": [{"text": "A new plant is expected to open.", "section_ids": [section.id]}],
        "risks": [{"text": "Customer concentration is material.", "section_ids": [section.id]}],
        "invalidation_conditions": [],
    }, "OLLAMA", "test-model"))
    analysis = analyze_document(db, document)
    result = analysis_out(db, analysis)
    assert result["status"] == "DRAFT"
    assert result["summary"]["section_ids"] == [section.id]
    assert result["citations"][str(section.id)]["excerpt"].startswith("Management")


def test_analysis_rejects_citations_from_another_document(monkeypatch):
    db = db_session()
    instrument = Instrument(exchange="NSE", symbol="XYZ", company_name="XYZ Ltd")
    db.add(instrument); db.commit()
    document = store_document(db, instrument_id=instrument.id, document_type="TRANSCRIPT",
        title="Call", report_date=date(2026, 6, 30), source_url=None,
        filename="call.txt", content_type="text/plain", content=b"Supported statement.")
    monkeypatch.setattr(document_analysis, "_invoke", lambda prompt: ({
        "summary": "Unsupported citation", "summary_section_ids": [999999],
        "catalysts": [], "risks": [], "invalidation_conditions": [],
    }, "OLLAMA", "test-model"))
    try:
        analyze_document(db, document)
        assert False, "invalid citation should fail"
    except document_analysis.DocumentAnalysisError as exc:
        assert "not in this document" in str(exc)
