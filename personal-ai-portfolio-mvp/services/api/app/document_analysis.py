from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import date, datetime

import httpx
from pydantic import BaseModel, Field, ValidationError
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import GroundedDocumentAnalysis, ResearchDocument, ResearchDocumentSection


class DocumentAnalysisError(ValueError):
    pass


class CitedClaim(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    section_ids: list[int] = Field(min_length=1, max_length=5)


class AnalysisPayload(BaseModel):
    summary: str = Field(min_length=1, max_length=12000)
    summary_section_ids: list[int] = Field(min_length=1, max_length=12)
    catalysts: list[CitedClaim] = Field(default_factory=list, max_length=20)
    risks: list[CitedClaim] = Field(default_factory=list, max_length=20)
    invalidation_conditions: list[CitedClaim] = Field(default_factory=list, max_length=20)


def extract_sections(content: bytes, content_type: str, filename: str) -> tuple[list[dict], int]:
    is_pdf = content_type == "application/pdf" or filename.lower().endswith(".pdf")
    if is_pdf:
        try:
            reader = PdfReader(io.BytesIO(content))
            sections = []
            for page_no, page in enumerate(reader.pages, 1):
                text = re.sub(r"[ \t]+", " ", page.extract_text() or "").strip()
                if text:
                    sections.append({"page_number": page_no, "heading": f"Page {page_no}", "text": text})
        except Exception as exc:
            raise DocumentAnalysisError(f"PDF text extraction failed: {exc}") from exc
        if not sections:
            raise DocumentAnalysisError("The PDF contains no extractable text; scanned PDFs require OCR before import.")
        return sections, len(reader.pages)
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DocumentAnalysisError("Transcripts must be UTF-8 text files.") from exc
    text = text.strip()
    if not text:
        raise DocumentAnalysisError("The uploaded document is empty.")
    chunks, current = [], []
    size = 0
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if current and size + len(paragraph) > 6000:
            chunks.append("\n\n".join(current)); current, size = [], 0
        current.append(paragraph); size += len(paragraph)
    if current:
        chunks.append("\n\n".join(current))
    return [{"page_number": None, "heading": f"Transcript section {i}", "text": value}
            for i, value in enumerate(chunks, 1)], 0


def store_document(db: Session, *, instrument_id: int, document_type: str, title: str,
                   report_date: date, source_url: str | None, filename: str,
                   content_type: str, content: bytes) -> ResearchDocument:
    digest = hashlib.sha256(content).hexdigest()
    existing = db.scalar(select(ResearchDocument).where(
        ResearchDocument.instrument_id == instrument_id, ResearchDocument.content_hash == digest))
    if existing:
        return existing
    extracted, page_count = extract_sections(content, content_type, filename)
    document = ResearchDocument(instrument_id=instrument_id, document_type=document_type,
        title=title, report_date=report_date, source_url=source_url, filename=filename,
        content_type=content_type, content_hash=digest, content=content, page_count=page_count)
    db.add(document); db.flush()
    for index, item in enumerate(extracted, 1):
        db.add(ResearchDocumentSection(document_id=document.id, section_index=index, **item))
    db.commit(); db.refresh(document)
    return document


def _invoke(prompt: str, schema: type[BaseModel] = AnalysisPayload) -> tuple[dict, str, str]:
    errors = []
    if settings.disclosure_llm_provider in {"auto", "ollama"}:
        try:
            response = httpx.post(f"{settings.ollama_base_url.rstrip('/')}/api/chat", json={
                "model": settings.ollama_model, "stream": False, "format": schema.model_json_schema(),
                "options": {"temperature": 0}, "messages": [{"role": "user", "content": prompt}],
            }, timeout=180)
            response.raise_for_status()
            return json.loads(response.json()["message"]["content"]), "OLLAMA", settings.ollama_model
        except Exception as exc:
            errors.append(f"Ollama: {exc}")
    if settings.disclosure_llm_provider in {"auto", "openrouter"} and settings.openrouter_api_key:
        try:
            response = httpx.post("https://openrouter.ai/api/v1/chat/completions", headers={
                "Authorization": f"Bearer {settings.openrouter_api_key}"}, json={
                "model": settings.openrouter_model, "temperature": 0,
                "response_format": {"type": "json_schema", "json_schema": {
                    "name": "grounded_document_analysis", "strict": True,
                    "schema": schema.model_json_schema()}},
                "messages": [{"role": "user", "content": prompt}]}, timeout=180)
            response.raise_for_status()
            return json.loads(response.json()["choices"][0]["message"]["content"]), "OPENROUTER", settings.openrouter_model
        except Exception as exc:
            errors.append(f"OpenRouter: {exc}")
    raise DocumentAnalysisError("No document-analysis model completed successfully. " + "; ".join(errors))


def analyze_document(db: Session, document: ResearchDocument, force: bool = False) -> GroundedDocumentAnalysis:
    latest = db.scalar(select(GroundedDocumentAnalysis).where(
        GroundedDocumentAnalysis.document_id == document.id).order_by(GroundedDocumentAnalysis.id.desc()))
    if latest and not force:
        return latest
    sections = db.scalars(select(ResearchDocumentSection).where(
        ResearchDocumentSection.document_id == document.id).order_by(ResearchDocumentSection.section_index)).all()
    remaining, evidence = settings.document_llm_max_chars, []
    for section in sections:
        block = f"\n[SECTION {section.id} | {section.heading}]\n{section.text}\n"
        if len(block) > remaining:
            break
        evidence.append(block); remaining -= len(block)
    if not evidence:
        raise DocumentAnalysisError("No document text fits within the configured analysis limit.")
    prompt = f"""Create a grounded research draft from the supplied document only.
Return JSON matching the supplied schema. Every claim must cite one or more numeric SECTION ids.
Do not calculate, derive, forecast, or alter figures; mention a numeric fact only as printed.
Do not give a buy/sell recommendation. Omit unsupported claims. Distinguish management statements
from established facts. Summary citations go in summary_section_ids.
Document: {document.title} ({document.document_type}, {document.report_date})
Evidence:{''.join(evidence)}"""
    raw, provider, model = _invoke(prompt)
    try:
        payload = AnalysisPayload.model_validate(raw)
    except ValidationError as exc:
        raise DocumentAnalysisError(f"Model output failed schema validation: {exc}") from exc
    valid_ids = {section.id for section in sections}
    cited = set(payload.summary_section_ids)
    for group in (payload.catalysts, payload.risks, payload.invalidation_conditions):
        for claim in group: cited.update(claim.section_ids)
    if not cited.issubset(valid_ids):
        raise DocumentAnalysisError("Model output cited a section that is not in this document.")
    analysis = GroundedDocumentAnalysis(document_id=document.id, summary=payload.summary,
        catalysts=[item.model_dump() for item in payload.catalysts],
        risks=[item.model_dump() for item in payload.risks],
        invalidation_conditions=[item.model_dump() for item in payload.invalidation_conditions],
        provider=provider, model=model, prompt_version=settings.document_prompt_version,
        evidence_section_ids=[section.id for section in sections[:len(evidence)]],
        omitted_section_count=max(0, len(sections) - len(evidence)))
    # Summary uses the same citation shape as all other grounded claims.
    analysis.summary = json.dumps({"text": payload.summary, "section_ids": payload.summary_section_ids})
    db.add(analysis); db.commit(); db.refresh(analysis)
    return analysis


def analysis_out(db: Session, analysis: GroundedDocumentAnalysis) -> dict:
    document = db.get(ResearchDocument, analysis.document_id)
    section_ids = set(json.loads(analysis.summary)["section_ids"])
    for group in (analysis.catalysts, analysis.risks, analysis.invalidation_conditions):
        for claim in group: section_ids.update(claim["section_ids"])
    sections = db.scalars(select(ResearchDocumentSection).where(ResearchDocumentSection.id.in_(section_ids))).all()
    citations = {str(s.id): {"section_id": s.id, "section_index": s.section_index,
        "page_number": s.page_number, "heading": s.heading, "excerpt": s.text[:1200]} for s in sections}
    return {"id": analysis.id, "document_id": analysis.document_id, "status": analysis.status,
        "summary": json.loads(analysis.summary), "catalysts": analysis.catalysts, "risks": analysis.risks,
        "invalidation_conditions": analysis.invalidation_conditions, "provider": analysis.provider,
        "model": analysis.model, "prompt_version": analysis.prompt_version,
        "evidence_section_count": len(analysis.evidence_section_ids),
        "omitted_section_count": analysis.omitted_section_count,
        "review_note": analysis.review_note, "reviewed_at": analysis.reviewed_at,
        "created_at": analysis.created_at, "citations": citations,
        "document": {"title": document.title, "report_date": document.report_date,
                     "document_type": document.document_type, "source_url": document.source_url}}
