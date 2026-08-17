from __future__ import annotations

import hashlib, re
from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx
from lxml import html
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .document_analysis import _invoke, extract_sections
from .models import IPOAnalysis, IPODocument, IPODocumentSection, IPOIssue, Price

SEBI_PUBLIC_ISSUES = (
    "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=3&smid=10&ssid=15",
    "https://www.sebi.gov.in/sebiweb/home/HomeAction.do?doListing=yes&sid=3&smid=11&ssid=15",
)
STAGES = ["DISCOVERED", "DRHP_FILED", "RHP_FILED", "PRICE_BAND_ANNOUNCED", "ISSUE_OPEN",
          "ISSUE_CLOSED", "ALLOTMENT", "LISTED", "POST_LISTING_MONITORING", "GRADUATED"]

class IPOError(ValueError): pass

def normalize_name(value: str) -> str:
    value = re.sub(r"\b(UDRHP|DRHP|RHP|PROSPECTUS|ADDENDUM|CORRIGENDUM|DRAFT|ABRIDGED|LIMITED|LTD)\b", " ", value.upper())
    return re.sub(r"[^A-Z0-9]+", " ", value).strip()

def _stage_from_title(title: str) -> str:
    upper = title.upper()
    if "RHP" in upper and "DRHP" not in upper: return "RHP_FILED"
    if "PROSPECTUS" in upper and "DRAFT" not in upper: return "RHP_FILED"
    return "DRHP_FILED"

def _filing_date(link) -> date:
    text = " ".join(link.getparent().text_content().split()) if link.getparent() is not None else ""
    match = re.search(r"\b([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4})\b", text)
    try: return datetime.strptime(match.group(1), "%b %d, %Y").date() if match else date.today()
    except ValueError: return date.today()

def discover_sebi_ipos(db: Session, client: httpx.Client | None = None) -> dict:
    owns = client is None; client = client or httpx.Client(timeout=30, follow_redirects=True,
        headers={"User-Agent": "PersonalPortfolioResearch/1.0"})
    created = updated = 0
    try:
        links = []
        for source in SEBI_PUBLIC_ISSUES:
            response = client.get(source); response.raise_for_status()
            tree = html.fromstring(response.content)
            links.extend(tree.xpath("//a[contains(@href, '/filings/public-issues/')]") )
        seen = set()
        for link in links:
            title = " ".join(link.text_content().split())
            href = link.get("href") or ""
            if not title or not re.search(r"\b(UDRHP|DRHP|RHP|PROSPECTUS)\b", title, re.I): continue
            if re.search(r"\b(REIT|INVIT|TRUST|DEBT|NCD)\b", title, re.I): continue
            source_url = href if href.startswith("http") else "https://www.sebi.gov.in" + href
            key = normalize_name(title); filed_on = _filing_date(link)
            if not key or (key, source_url) in seen: continue
            seen.add((key, source_url)); stage = _stage_from_title(title)
            item = db.scalar(select(IPOIssue).where(IPOIssue.normalized_name == key))
            if not item:
                item = IPOIssue(company_name=re.sub(r"\s*[-–]?\s*(UDRHP|DRHP|RHP|DRAFT\s+ABRIDGED\s+PROSPECTUS|PROSPECTUS).*$", "", title,
                    flags=re.I).strip(), normalized_name=key, board="UNCLASSIFIED", stage=stage, discovered_on=filed_on,
                    drhp_date=filed_on if stage == "DRHP_FILED" else None,
                    rhp_date=filed_on if stage == "RHP_FILED" else None,
                    source_url=source_url, source_type="SEBI")
                db.add(item); created += 1
            else:
                if item.stage in STAGES and STAGES.index(stage) > STAGES.index(item.stage): item.stage = stage
                item.source_url, item.last_checked_at = source_url, datetime.utcnow(); updated += 1
        db.commit()
        return {"status": "SUCCESS", "sources": list(SEBI_PUBLIC_ISSUES), "created": created,
                "updated": updated, "records_seen": len(seen)}
    except Exception as exc:
        raise IPOError(f"SEBI IPO discovery failed: {exc}") from exc
    finally:
        if owns: client.close()

def store_ipo_document(db: Session, issue: IPOIssue, *, document_type: str, document_date: date,
                       title: str, source_url: str, content: bytes) -> IPODocument:
    if not source_url.startswith("https://"): raise IPOError("Official source URL must use HTTPS.")
    digest = hashlib.sha256(content).hexdigest()
    existing = db.scalar(select(IPODocument).where(IPODocument.ipo_id == issue.id,
                                                    IPODocument.content_hash == digest))
    if existing: return existing
    sections, page_count = extract_sections(content, "application/pdf", "document.pdf")
    doc = IPODocument(ipo_id=issue.id, document_type=document_type.upper(), document_date=document_date,
        title=title, source_url=source_url, content_hash=digest, content=content, page_count=page_count)
    db.add(doc); db.flush()
    for index, section in enumerate(sections, 1):
        db.add(IPODocumentSection(document_id=doc.id, section_index=index, **section))
    stage = "RHP_FILED" if document_type.upper() in {"RHP", "PROSPECTUS"} else "DRHP_FILED"
    if STAGES.index(stage) > STAGES.index(issue.stage): issue.stage = stage
    if stage == "RHP_FILED": issue.rhp_date = document_date
    else: issue.drhp_date = document_date
    db.commit(); db.refresh(doc); return doc

class Claim(BaseModel):
    text: str = Field(min_length=1, max_length=2500)
    section_ids: list[int] = Field(min_length=1, max_length=6)
class IPOPayload(BaseModel):
    summary: Claim
    offer_structure: list[Claim] = []
    use_of_proceeds: list[Claim] = []
    financial_quality: list[Claim] = []
    related_parties_and_governance: list[Claim] = []
    litigation_and_auditor: list[Claim] = []
    material_risks: list[Claim] = []
    peer_valuation: list[Claim] = []
    catalysts: list[Claim] = []
    proposed_outcome: str

def analyze_ipo(db: Session, issue: IPOIssue, document: IPODocument) -> IPOAnalysis:
    sections = db.scalars(select(IPODocumentSection).where(
        IPODocumentSection.document_id == document.id).order_by(IPODocumentSection.section_index)).all()
    evidence, chars = [], 0
    for section in sections:
        block = f"\n[SECTION {section.id} | page {section.page_number}]\n{section.text}\n"
        if chars + len(block) > 90000: break
        evidence.append(block); chars += len(block)
    prompt = """Analyze this Indian IPO offer document using only supplied evidence. Return the JSON schema.
Every statement requires section_ids. Extract offer structure, use of proceeds, financial quality,
related parties/governance, litigation/auditor qualifications, material risks, disclosed peers and
valuation, and catalysts. Do no arithmetic and do not invent missing values. proposed_outcome must
be AVOID, WATCH, or CONSIDER and remains a human-review draft. Evidence:""" + "".join(evidence)
    raw, provider, model = _invoke(prompt, IPOPayload)
    payload = IPOPayload.model_validate(raw)
    if payload.proposed_outcome not in {"AVOID", "WATCH", "CONSIDER"}: raise IPOError("Invalid outcome.")
    valid = {section.id for section in sections}
    claims = [payload.summary]
    for key in ("offer_structure", "use_of_proceeds", "financial_quality",
                "related_parties_and_governance", "litigation_and_auditor", "material_risks",
                "peer_valuation", "catalysts"): claims.extend(getattr(payload, key))
    if any(not set(claim.section_ids).issubset(valid) for claim in claims):
        raise IPOError("Analysis cited evidence outside this document.")
    item = IPOAnalysis(ipo_id=issue.id, document_id=document.id, outcome=payload.proposed_outcome,
        payload=payload.model_dump(), provider=provider, model=model)
    db.add(item); db.commit(); db.refresh(item); return item

def post_listing_monitor(db: Session, issue: IPOIssue) -> dict:
    if not issue.instrument_id or not issue.listing_date or issue.issue_price is None:
        return {"status": "NOT_LISTED", "milestones": []}
    prices = db.scalars(select(Price).where(Price.instrument_id == issue.instrument_id,
        Price.price_date >= issue.listing_date).order_by(Price.price_date)).all()
    if not prices: return {"status": "WAITING_FOR_PRICE", "milestones": []}
    latest = prices[-1]; issue_price = Decimal(issue.issue_price)
    milestones = []
    for days in (30, 90, 180, 365):
        target = issue.listing_date + timedelta(days=days)
        observed = next((item for item in prices if item.price_date >= target), None)
        milestones.append({"days": days, "target_date": target, "status": "AVAILABLE" if observed else "PENDING",
            "observed_date": observed.price_date if observed else None,
            "return_from_issue": Decimal(observed.close_price) / issue_price - 1 if observed else None})
    age = (date.today() - issue.listing_date).days
    if age >= 365: issue.stage = "GRADUATED"
    elif issue.stage == "LISTED": issue.stage = "POST_LISTING_MONITORING"
    db.commit()
    return {"status": "GRADUATED" if age >= 365 else "MONITORING", "days_since_listing": age,
        "latest_price": latest.close_price, "latest_price_date": latest.price_date,
        "return_from_issue": Decimal(latest.close_price) / issue_price - 1,
        "maximum_drawdown_from_issue": min(Decimal(item.close_price) for item in prices) / issue_price - 1,
        "milestones": milestones,
        "limitations": ["Returns use stored closes and exclude subscription availability, costs, and dividends.",
                        "Limited history is not used for mature-company technical conclusions."]}
