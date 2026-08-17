from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.database import Base
from app import ipo_lifecycle
from app.ipo_lifecycle import analyze_ipo, discover_sebi_ipos, post_listing_monitor
from app.models import IPOIssue, IPODocument, IPODocumentSection, Instrument, Price

def db_session():
    engine = create_engine("sqlite+pysqlite:///:memory:"); Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()

def test_official_discovery_deduplicates_drhp_and_rhp_and_advances_stage():
    db = db_session()
    body = b'''<a href="/filings/public-issues/a.html">Example Limited - DRHP</a>
               <a href="/filings/public-issues/b.html">Example Limited - RHP</a>'''
    response = SimpleNamespace(content=body, raise_for_status=lambda: None)
    client = SimpleNamespace(get=lambda url: response)
    result = discover_sebi_ipos(db, client)
    assert result["created"] == 1
    assert db.query(IPOIssue).one().stage == "RHP_FILED"

def test_cited_ipo_draft_is_schema_validated(monkeypatch):
    db = db_session()
    issue = IPOIssue(company_name="Example", normalized_name="EXAMPLE", stage="DRHP_FILED",
        discovered_on=date.today(), source_url="https://www.sebi.gov.in/example")
    db.add(issue); db.flush()
    doc = IPODocument(ipo_id=issue.id, document_type="DRHP", document_date=date.today(), title="DRHP",
        source_url="https://www.sebi.gov.in/example.pdf", content_hash="a"*64, content=b"x", page_count=1)
    db.add(doc); db.flush()
    section = IPODocumentSection(document_id=doc.id, section_index=1, page_number=1,
                                  heading="Page 1", text="Fresh issue proceeds fund expansion; litigation is disclosed.")
    db.add(section); db.commit()
    claim = {"text":"Expansion is a stated use of proceeds.", "section_ids":[section.id]}
    monkeypatch.setattr(ipo_lifecycle, "_invoke", lambda prompt, schema: ({"summary":claim,
        "offer_structure":[claim], "use_of_proceeds":[claim], "financial_quality":[],
        "related_parties_and_governance":[], "litigation_and_auditor":[], "material_risks":[],
        "peer_valuation":[], "catalysts":[], "proposed_outcome":"WATCH"}, "OLLAMA", "test"))
    assert analyze_ipo(db, issue, doc).outcome == "WATCH"

def test_post_listing_milestones_and_graduation():
    db = db_session()
    instrument = Instrument(exchange="NSE", symbol="IPO", company_name="IPO Ltd")
    db.add(instrument); db.flush()
    issue = IPOIssue(company_name="IPO", normalized_name="IPO", stage="LISTED",
        discovered_on=date(2024,1,1), source_url="https://www.sebi.gov.in/ipo",
        instrument_id=instrument.id, listing_date=date(2024,1,1), issue_price=100)
    db.add(issue)
    db.add_all([Price(instrument_id=instrument.id, price_date=date(2024,1,1), close_price=100, source="TEST"),
                Price(instrument_id=instrument.id, price_date=date(2025,1,2), close_price=125, source="TEST")])
    db.commit(); result = post_listing_monitor(db, issue)
    assert result["status"] == "GRADUATED"
    assert result["return_from_issue"] == Decimal("0.25")
