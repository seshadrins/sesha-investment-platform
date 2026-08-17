from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from lxml import etree, html
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .config import settings
from .investor_following import load_investor_config
from .models import (
    AppNotification,
    DisclosureDocument,
    DisclosureSourceMapping,
    Instrument,
    InvestorAliasReview,
    InvestorDisclosure,
)


PARSER_VERSION = "phase5-xbrl-v1"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class DisclosurePipelineError(RuntimeError):
    pass


@dataclass(frozen=True)
class FilingMetadata:
    exchange: str
    source_code: str
    report_date: date
    filed_on: date | None
    source_url: str
    content_type: str = "application/xml"


class ExtractedObservation(BaseModel):
    shareholder_name: str = Field(min_length=2, max_length=300)
    ownership_pct: Decimal = Field(ge=0, le=100)
    shares: Decimal | None = Field(default=None, ge=0)


class ExtractedObservationList(BaseModel):
    observations: list[ExtractedObservation]


def normalize_investor_name(value: str) -> str:
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", text.upper()).split())


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace("Sept", "Sep")
    for fmt in (
        "%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d-%b-%Y", "%d-%b-%y",
        "%d/%m/%Y", "%b %d %Y %I:%M%p", "%b %d %Y", "%B %Y",
    ):
        try:
            parsed = datetime.strptime(text[:19], fmt)
            if fmt == "%B %Y":
                month = parsed.month
                next_month = date(parsed.year + (month == 12), month % 12 + 1, 1)
                return next_month - timedelta(days=1)
            return parsed.date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _official_url(url: str, exchange: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    allowed = "bseindia.com" if exchange == "BSE" else "nseindia.com"
    if parsed.scheme != "https" or not (host == allowed or host.endswith("." + allowed)):
        raise DisclosurePipelineError(
            f"Rejected non-official {exchange} disclosure URL: {url}"
        )
    return url


def _nested_rows(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _nested_rows(child)
    elif isinstance(value, list):
        for child in value:
            yield from _nested_rows(child)


def _value(row: dict, *names: str) -> Any:
    lookup = {str(key).replace("_", "").lower(): value for key, value in row.items()}
    for name in names:
        found = lookup.get(name.replace("_", "").lower())
        if found not in (None, ""):
            return found
    return None


class ExchangeDisclosureClient:
    """Polite, official-host-only discovery and download client."""

    def __init__(self, client: httpx.Client | None = None, interval_seconds: float | None = None):
        self._owns_client = client is None
        self.client = client or httpx.Client(
            timeout=35, follow_redirects=True, headers={"User-Agent": USER_AGENT}
        )
        self.interval = (
            settings.disclosure_request_interval_seconds
            if interval_seconds is None else interval_seconds
        )
        self._last_request_at = 0.0

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def _wait(self) -> None:
        remaining = self.interval - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)

    def _get(self, url: str, exchange: str, **kwargs) -> httpx.Response:
        _official_url(url, exchange)
        self._wait()
        try:
            response = self.client.get(url, **kwargs)
            self._last_request_at = time.monotonic()
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DisclosurePipelineError(f"{exchange} request failed: {exc}") from exc
        if len(response.content) > 12 * 1024 * 1024:
            raise DisclosurePipelineError(f"{exchange} filing exceeded the 12 MB safety limit.")
        return response

    def discover(self, mapping: DisclosureSourceMapping, period: date) -> list[FilingMetadata]:
        if mapping.exchange == "BSE":
            return self._discover_bse(mapping.source_code, period)
        if mapping.exchange == "NSE":
            return self._discover_nse(mapping.source_code, period)
        raise DisclosurePipelineError(f"Unsupported disclosure exchange: {mapping.exchange}")

    def _discover_bse(self, source_code: str, period: date) -> list[FilingMetadata]:
        response = self._get(
            settings.bse_disclosure_api_url, "BSE",
            # BSE's current UI defines flag 6 as "Last 1 year". The scheduler only
            # requests the latest due quarter, so this bounded window is sufficient
            # and avoids downloading an issuer's complete filing history.
            params={"scripcode": source_code, "flag": "6", "indtype": ""},
            headers={
                "User-Agent": USER_AGENT,
                "Referer": "https://www.bseindia.com/",
                "Accept": "application/json, text/plain, */*",
            },
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise DisclosurePipelineError(
                "BSE returned a non-JSON response; access may be throttled or its interface changed."
            ) from exc
        if not isinstance(body, dict) or "Table" not in body:
            raise DisclosurePipelineError(
                "BSE returned an empty or unexpected response; treating it as a throttled/source "
                "failure so the bounded scheduler retry can run."
            )
        filings = []
        for row in body.get("Table") or []:
            report_date = _parse_date(_value(row, "EndDate", "DisplayDT"))
            attachment = _value(row, "XBRLAttachment")
            if report_date != period or not attachment or str(row.get("IsXBRL", 1)) == "0":
                continue
            url = urljoin("https://www.bseindia.com/", str(attachment))
            filings.append(FilingMetadata(
                "BSE", source_code, report_date,
                _parse_date(_value(row, "D", "broadcastTime")), _official_url(url, "BSE"),
                "text/html",
            ))
        return filings

    def _discover_nse(self, source_code: str, period: date) -> list[FilingMetadata]:
        landing = "https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern"
        self._get(landing, "NSE", headers={"User-Agent": USER_AGENT})
        response = self._get(
            settings.nse_disclosure_api_url, "NSE",
            params={
                "index": "equities", "symbol": source_code,
                "from_date": period.strftime("%d-%m-%Y"),
                "to_date": (period + timedelta(days=35)).strftime("%d-%m-%Y"),
            },
            headers={
                "User-Agent": USER_AGENT, "Referer": landing,
                "Accept": "application/json, text/plain, */*",
            },
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise DisclosurePipelineError(
                "NSE returned a non-JSON response; no anti-bot controls were bypassed. "
                "Add the stock's BSE source code to use the BSE recovery adapter."
            ) from exc
        filings = []
        seen = set()
        for row in _nested_rows(body):
            report_date = _parse_date(_value(
                row, "asOnDate", "dateOfReporting", "displayDate", "endDate", "date"
            ))
            attachment = _value(
                row, "xbrlFileLink", "xbrlFile", "xbrl", "attachment", "fileUrl"
            )
            if report_date != period or not attachment:
                continue
            url = urljoin("https://www.nseindia.com/", str(attachment))
            if url in seen:
                continue
            seen.add(url)
            filings.append(FilingMetadata(
                "NSE", source_code, report_date,
                _parse_date(_value(row, "submissionDate", "broadcastDateTime", "filedOn")),
                _official_url(url, "NSE"), "application/xml",
            ))
        return filings

    def download(self, filing: FilingMetadata) -> tuple[bytes, str]:
        referer = (
            "https://www.bseindia.com/" if filing.exchange == "BSE"
            else "https://www.nseindia.com/companies-listing/corporate-filings-shareholding-pattern"
        )
        response = self._get(
            filing.source_url, filing.exchange,
            headers={"User-Agent": USER_AGENT, "Referer": referer},
        )
        content_type = response.headers.get("content-type", filing.content_type).split(";")[0]
        if not response.content.strip():
            raise DisclosurePipelineError("The exchange returned an empty disclosure document.")
        return response.content, content_type


def _local_name(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].rsplit(":", 1)[-1].lower()


def _concept_name(element) -> str:
    name = element.attrib.get("name") or element.attrib.get("Name") or _local_name(element.tag)
    return re.sub(r"[^a-z0-9]", "", name.rsplit(":", 1)[-1].lower())


def _fact_text(element) -> str:
    return " ".join("".join(element.itertext()).replace("\xa0", " ").split())


def _decimal(value: str, element=None) -> Decimal | None:
    text = value.replace(",", "").replace("%", "").strip()
    if text in {"", "-", "--", "NIL", "NA"}:
        return None
    try:
        result = Decimal(text)
        if element is not None:
            scale = int(element.attrib.get("scale", "0"))
            result *= Decimal(10) ** scale
            if element.attrib.get("sign") == "-":
                result *= -1
        return result
    except (InvalidOperation, ValueError):
        return None


def extract_xbrl_observations(content: bytes) -> list[ExtractedObservation]:
    """Extract shareholder rows from XML or inline-XBRL without taxonomy-version coupling."""
    roots = []
    try:
        roots.append(etree.fromstring(content, etree.XMLParser(recover=True, huge_tree=True)))
    except etree.Error:
        pass
    try:
        roots.append(html.fromstring(content))
    except (etree.Error, ValueError):
        pass
    for root in roots:
        grouped: dict[str, list[tuple[str, str, Any]]] = {}
        for element in root.iter():
            context = (
                element.attrib.get("contextRef") or element.attrib.get("contextref")
                or element.attrib.get("context-ref")
            )
            concept = _concept_name(element)
            if not context or not concept:
                continue
            # BSE's current taxonomy stores text facts (notably the shareholder
            # name) in a D_-prefixed context and numeric facts in the otherwise
            # identical context. They represent one disclosed table row.
            row_context = context[2:] if context.startswith("D_") else context
            grouped.setdefault(row_context, []).append((concept, _fact_text(element), element))
        results: list[ExtractedObservation] = []
        seen = set()
        for facts in grouped.values():
            name = None
            shares = None
            percentage = None
            for concept, value, element in facts:
                if "nameoftheshareholder" in concept or "nameofshareholder" in concept:
                    name = value
                elif percentage is None and (
                    "shareholdingpercentage" in concept
                    or "shareholdingasapercentage" in concept
                    or concept.endswith("percentageofshareholding")
                ):
                    # The UI-rendered XBRL value is in percentage points (for
                    # example 0.12%). BSE uses scale=-2 to encode the underlying
                    # pure ratio; applying it again would store 0.0012%.
                    percentage = _decimal(value)
                    if percentage is not None and _local_name(element.tag) not in {
                        "nonfraction", "nonnumeric"
                    }:
                        # Plain NSE XBRL stores a pure ratio (0.0125 for 1.25%).
                        percentage *= 100
                elif shares is None and (
                    concept == "numberofshares" or concept.endswith("totalnumbersofsharesheld")
                    or concept.endswith("totalnossharesheld")
                ):
                    shares = _decimal(value, element)
            if not name or percentage is None or not Decimal("0") <= percentage <= Decimal("100"):
                continue
            key = (normalize_investor_name(name), percentage, shares)
            if key in seen:
                continue
            seen.add(key)
            results.append(ExtractedObservation(
                shareholder_name=name, ownership_pct=percentage, shares=shares
            ))
        if results:
            return results
    return []


def _document_text(content: bytes) -> str:
    try:
        root = html.fromstring(content)
        text = " ".join(root.text_content().split())
    except (etree.Error, ValueError):
        text = content.decode("utf-8", errors="replace")
    return text[:settings.disclosure_llm_max_chars]


def _llm_prompt(content: bytes) -> str:
    aliases = {
        profile["id"]: profile["aliases"] for profile in load_investor_config()["investors"]
    }
    return (
        "Extract only explicitly named shareholder observations from this official shareholding "
        "filing. Return JSON matching the supplied schema. Do not infer an exit from an absent "
        "name and do not calculate or guess missing values. Only retain names plausibly matching "
        f"these configured aliases: {json.dumps(aliases)}.\n\nDOCUMENT:\n{_document_text(content)}"
    )


def _parse_llm_body(raw: Any) -> list[ExtractedObservation]:
    if isinstance(raw, str):
        raw = json.loads(raw)
    return ExtractedObservationList.model_validate(raw).observations


def extract_with_llm(
    content: bytes, client: httpx.Client | None = None
) -> tuple[list[ExtractedObservation], str | None, list[str]]:
    provider = settings.disclosure_llm_provider.strip().lower()
    if provider in {"none", "disabled", "off"}:
        return [], None, ["LLM fallback is disabled."]
    owns_client = client is None
    http = client or httpx.Client(timeout=90, follow_redirects=True)
    errors = []
    schema = ExtractedObservationList.model_json_schema()
    candidates = ["ollama", "openrouter"] if provider == "auto" else [provider]
    try:
        for candidate in candidates:
            try:
                if candidate == "ollama":
                    tags = http.get(f"{settings.ollama_base_url.rstrip('/')}/api/tags")
                    tags.raise_for_status()
                    available = {
                        item.get("name") for item in tags.json().get("models", [])
                    }
                    if settings.ollama_model not in available and not any(
                        (name or "").split(":")[0] == settings.ollama_model.split(":")[0]
                        for name in available
                    ):
                        raise DisclosurePipelineError(
                            f"Ollama model '{settings.ollama_model}' is not installed."
                        )
                    response = http.post(
                        f"{settings.ollama_base_url.rstrip('/')}/api/chat",
                        json={
                            "model": settings.ollama_model,
                            "messages": [{"role": "user", "content": _llm_prompt(content)}],
                            "format": schema, "stream": False,
                            "options": {"temperature": 0},
                        },
                    )
                    response.raise_for_status()
                    raw = response.json()["message"]["content"]
                    return _parse_llm_body(raw), f"OLLAMA:{settings.ollama_model}", errors
                if candidate == "openrouter":
                    if not settings.openrouter_api_key:
                        raise DisclosurePipelineError("OPENROUTER_API_KEY is not configured.")
                    if settings.openrouter_model == "openrouter/free":
                        raise DisclosurePipelineError(
                            "A pinned OpenRouter model is required; random free-model routing is disabled."
                        )
                    response = http.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {settings.openrouter_api_key}"},
                        json={
                            "model": settings.openrouter_model,
                            "temperature": 0,
                            "provider": {"data_collection": "deny", "allow_fallbacks": True},
                            "messages": [{"role": "user", "content": _llm_prompt(content)}],
                            "response_format": {
                                "type": "json_schema",
                                "json_schema": {"name": "investor_disclosures", "schema": schema},
                            },
                        },
                    )
                    response.raise_for_status()
                    raw = response.json()["choices"][0]["message"]["content"]
                    return _parse_llm_body(raw), f"OPENROUTER:{settings.openrouter_model}", errors
                raise DisclosurePipelineError(f"Unknown DISCLOSURE_LLM_PROVIDER: {candidate}")
            except (httpx.HTTPError, KeyError, ValueError, ValidationError, DisclosurePipelineError) as exc:
                errors.append(f"{candidate}: {exc}")
        return [], None, errors
    finally:
        if owns_client:
            http.close()


def _alias_maps(db: Session) -> tuple[dict[str, set[str]], dict[str, dict]]:
    config = load_investor_config()
    profiles = {profile["id"]: profile for profile in config["investors"]}
    aliases: dict[str, set[str]] = {}
    for profile in profiles.values():
        for alias in [profile["name"], *profile.get("aliases", [])]:
            aliases.setdefault(normalize_investor_name(alias), set()).add(profile["id"])
    approved = db.scalars(select(InvestorAliasReview).where(
        InvestorAliasReview.status == "APPROVED"
    )).all()
    for review in approved:
        if review.proposed_investor_id in profiles:
            aliases.setdefault(review.normalized_name, set()).add(review.proposed_investor_id)
    return aliases, profiles


def match_investor_alias(
    observed_name: str, aliases: dict[str, set[str]], threshold: float | None = None
) -> tuple[str | None, float, bool]:
    normalized = normalize_investor_name(observed_name)
    exact = aliases.get(normalized, set())
    if len(exact) == 1:
        return next(iter(exact)), 1.0, False
    if len(exact) > 1:
        return sorted(exact)[0], 1.0, True
    threshold = settings.disclosure_fuzzy_match_threshold if threshold is None else threshold
    observed_tokens = set(normalized.split())
    ranked = []
    for alias, investor_ids in aliases.items():
        if not observed_tokens.intersection(alias.split()):
            continue
        score = SequenceMatcher(None, normalized, alias).ratio()
        for investor_id in investor_ids:
            ranked.append((score, investor_id))
    ranked.sort(reverse=True)
    if not ranked or ranked[0][0] < threshold:
        return None, ranked[0][0] if ranked else 0.0, False
    ambiguous = len(ranked) > 1 and ranked[1][1] != ranked[0][1] and ranked[1][0] >= ranked[0][0] - .03
    return ranked[0][1], ranked[0][0], True or ambiguous


def _notification(
    db: Session, event_key: str, category: str, title: str, message: str,
    payload: dict, severity: str = "INFO",
) -> AppNotification | None:
    if db.scalar(select(AppNotification).where(AppNotification.event_key == event_key)):
        return None
    item = AppNotification(
        event_key=event_key, category=category, severity=severity,
        title=title, message=message, payload=payload,
    )
    db.add(item)
    return item


def _upsert_disclosure(
    db: Session, document: DisclosureDocument, observation: ExtractedObservation,
    investor_id: str, parser: str,
) -> str:
    existing = db.scalar(select(InvestorDisclosure).where(
        InvestorDisclosure.investor_id == investor_id,
        InvestorDisclosure.instrument_id == document.instrument_id,
        InvestorDisclosure.report_date == document.report_date,
    ))
    old = None if not existing else (existing.ownership_pct, existing.shares, existing.source_url)
    if existing:
        existing.filed_on = document.filed_on
        existing.ownership_pct = observation.ownership_pct
        existing.shares = observation.shares
        existing.source_url = document.source_url
        existing.source_type = f"{document.exchange}_{parser}"[:40]
        action = "UPDATED" if old != (
            observation.ownership_pct, observation.shares, document.source_url
        ) else "UNCHANGED"
    else:
        db.add(InvestorDisclosure(
            investor_id=investor_id, instrument_id=document.instrument_id,
            report_date=document.report_date, filed_on=document.filed_on,
            ownership_pct=observation.ownership_pct, shares=observation.shares,
            source_url=document.source_url, source_type=f"{document.exchange}_{parser}"[:40],
        ))
        action = "NEW"
    if action != "UNCHANGED":
        digest = hashlib.sha256(
            f"{investor_id}|{document.instrument_id}|{document.report_date}|"
            f"{observation.ownership_pct}|{observation.shares}".encode()
        ).hexdigest()[:20]
        _notification(
            db, f"investor-disclosure:{digest}", "INVESTOR_DISCLOSURE",
            f"Followed-investor disclosure {action.lower()}",
            f"{investor_id} reported {observation.ownership_pct}% ownership for "
            f"{document.report_date.isoformat()}.",
            {
                "investor_id": investor_id, "instrument_id": document.instrument_id,
                "report_date": document.report_date.isoformat(),
                "ownership_pct": float(observation.ownership_pct),
                "source_url": document.source_url, "change_type": action,
            },
        )
    return action


def _create_review(
    db: Session, document: DisclosureDocument, observation: ExtractedObservation,
    investor_id: str, confidence: float, parser: str,
) -> bool:
    normalized = normalize_investor_name(observation.shareholder_name)
    existing = db.scalar(select(InvestorAliasReview).where(
        InvestorAliasReview.document_id == document.id,
        InvestorAliasReview.observed_name == observation.shareholder_name,
        InvestorAliasReview.proposed_investor_id == investor_id,
    ))
    if existing:
        return False
    review = InvestorAliasReview(
        document_id=document.id, instrument_id=document.instrument_id,
        observed_name=observation.shareholder_name, normalized_name=normalized,
        proposed_investor_id=investor_id, confidence=Decimal(str(round(confidence, 5))),
        report_date=document.report_date, filed_on=document.filed_on,
        ownership_pct=observation.ownership_pct, shares=observation.shares,
        source_url=document.source_url, parser=parser,
    )
    db.add(review)
    db.flush()
    _notification(
        db, f"alias-review:{review.id}", "ALIAS_REVIEW", "Investor alias needs review",
        f"'{observation.shareholder_name}' may refer to {investor_id}.",
        {"review_id": review.id, "confidence": confidence, "source_url": document.source_url},
        "WARNING",
    )
    return True


def _process_document(db: Session, document: DisclosureDocument) -> dict:
    observations = extract_xbrl_observations(document.content or b"")
    parser = "XBRL"
    llm_errors: list[str] = []
    if not observations:
        observations, llm_parser, llm_errors = extract_with_llm(document.content or b"")
        parser = llm_parser or "NO_PARSER"
    aliases, _ = _alias_maps(db)
    matched = reviews = 0
    actions = {"NEW": 0, "UPDATED": 0, "UNCHANGED": 0}
    for observation in observations:
        investor_id, confidence, needs_review = match_investor_alias(
            observation.shareholder_name, aliases
        )
        if not investor_id:
            continue
        if needs_review:
            reviews += int(_create_review(
                db, document, observation, investor_id, confidence, parser
            ))
            continue
        action = _upsert_disclosure(db, document, observation, investor_id, parser)
        actions[action] += 1
        matched += 1
    document.parser = parser
    document.parser_version = PARSER_VERSION
    document.extracted_count = len(observations)
    document.matched_count = matched
    document.processed_at = datetime.utcnow()
    document.error = "; ".join(llm_errors) or None
    document.status = (
        "PARSED" if matched else "REVIEW_REQUIRED" if reviews else
        "NO_FOLLOWED_INVESTOR" if observations else "PARSER_FAILED"
    )
    return {
        "document_id": document.id, "status": document.status,
        "parser": parser, "extracted": len(observations), "matched": matched,
        "reviews": reviews, "actions": actions, "errors": llm_errors,
    }


def ensure_default_source_mappings(db: Session) -> int:
    existing = {(item.instrument_id, item.exchange) for item in db.scalars(
        select(DisclosureSourceMapping)
    ).all()}
    created = 0
    for instrument in db.scalars(select(Instrument).where(
        Instrument.exchange.in_(["NSE", "BSE"]), Instrument.isin.is_not(None)
    ).order_by(Instrument.id)).all():
        exchange = instrument.exchange.upper()
        if (instrument.id, exchange) in existing:
            continue
        if exchange == "BSE" and not instrument.symbol.isdigit():
            continue
        db.add(DisclosureSourceMapping(
            instrument_id=instrument.id, exchange=exchange, source_code=instrument.symbol
        ))
        existing.add((instrument.id, exchange))
        created += 1
    if created:
        db.commit()
    return created


def upsert_source_mapping(
    db: Session, instrument_id: int, exchange: str, source_code: str, active: bool = True
) -> DisclosureSourceMapping:
    instrument = db.get(Instrument, instrument_id)
    exchange = exchange.strip().upper()
    source_code = source_code.strip().upper()
    if not instrument:
        raise DisclosurePipelineError("Instrument not found.")
    if exchange not in {"NSE", "BSE"}:
        raise DisclosurePipelineError("exchange must be NSE or BSE.")
    if exchange == "BSE" and not re.fullmatch(r"\d{6}", source_code):
        raise DisclosurePipelineError("BSE source_code must be the six-digit BSE scrip code.")
    if exchange == "NSE" and not re.fullmatch(r"[A-Z0-9&.-]{1,40}", source_code):
        raise DisclosurePipelineError("NSE source_code is not a valid exchange symbol.")
    mapping = db.scalar(select(DisclosureSourceMapping).where(
        DisclosureSourceMapping.instrument_id == instrument_id,
        DisclosureSourceMapping.exchange == exchange,
    ))
    if mapping:
        mapping.source_code = source_code
        mapping.active = active
        mapping.last_status = "PENDING"
        mapping.last_error = None
    else:
        mapping = DisclosureSourceMapping(
            instrument_id=instrument_id, exchange=exchange,
            source_code=source_code, active=active,
        )
        db.add(mapping)
    db.commit()
    db.refresh(mapping)
    return mapping


def run_disclosure_ingestion(
    db: Session, period: date, force: bool = False,
    mapping_ids: list[int] | None = None, client: ExchangeDisclosureClient | None = None,
) -> dict:
    ensure_default_source_mappings(db)
    query = select(DisclosureSourceMapping).where(DisclosureSourceMapping.active.is_(True))
    if mapping_ids:
        query = query.where(DisclosureSourceMapping.id.in_(mapping_ids))
    elif not force:
        cutoff = datetime.utcnow() - timedelta(hours=settings.disclosure_cache_hours)
        query = query.where(or_(
            DisclosureSourceMapping.last_checked_at.is_(None),
            DisclosureSourceMapping.last_checked_at < cutoff,
        ))
    mappings = db.scalars(query.order_by(
        DisclosureSourceMapping.last_checked_at.asc().nullsfirst(),
        DisclosureSourceMapping.id,
    ).limit(settings.disclosure_batch_size)).all()
    exchange_client = client or ExchangeDisclosureClient()
    owns_client = client is None
    stats = {
        "status": "SUCCESS", "period": period.isoformat(), "checked": 0,
        "discovered": 0, "processed": 0, "cached": 0, "matched": 0,
        "reviews_created": 0, "errors": [], "mapping_ids": [],
    }
    try:
        for mapping in mappings:
            stats["checked"] += 1
            stats["mapping_ids"].append(mapping.id)
            try:
                filings = exchange_client.discover(mapping, period)
                stats["discovered"] += len(filings)
                for filing in filings:
                    document = db.scalar(select(DisclosureDocument).where(
                        DisclosureDocument.source_url == filing.source_url
                    ))
                    if document and document.status in {
                        "PARSED", "REVIEW_REQUIRED", "NO_FOLLOWED_INVESTOR"
                    } and not force:
                        stats["cached"] += 1
                        continue
                    if not document:
                        document = DisclosureDocument(
                            mapping_id=mapping.id, instrument_id=mapping.instrument_id,
                            exchange=filing.exchange, report_date=filing.report_date,
                            filed_on=filing.filed_on, source_url=filing.source_url,
                            content_type=filing.content_type,
                        )
                        db.add(document)
                        db.flush()
                    content, content_type = exchange_client.download(filing)
                    document.content = content
                    document.content_type = content_type
                    document.content_hash = hashlib.sha256(content).hexdigest()
                    document.status = "DOWNLOADED"
                    result = _process_document(db, document)
                    stats["processed"] += 1
                    stats["matched"] += result["matched"]
                    stats["reviews_created"] += result["reviews"]
                mapping.last_status = "SUCCESS" if filings else "NO_FILING_FOR_PERIOD"
                mapping.last_error = None
                mapping.last_checked_at = datetime.utcnow()
                mapping.last_report_period = period
                db.commit()
            except Exception as exc:
                db.rollback()
                mapping = db.get(DisclosureSourceMapping, mapping.id)
                mapping.last_status = "FAILED"
                mapping.last_error = str(exc)[:2000]
                mapping.last_checked_at = datetime.utcnow()
                mapping.last_report_period = period
                db.commit()
                stats["errors"].append({
                    "mapping_id": mapping.id, "exchange": mapping.exchange,
                    "source_code": mapping.source_code, "error": str(exc),
                })
        if stats["errors"] and stats["checked"] == len(stats["errors"]):
            stats["status"] = "FAILED"
        elif stats["errors"]:
            stats["status"] = "PARTIAL"
        deliver_notifications(db)
        return stats
    finally:
        if owns_client:
            exchange_client.close()


def decide_alias_review(
    db: Session, review_id: int, decision: str, investor_id: str | None = None,
    note: str | None = None,
) -> InvestorAliasReview:
    review = db.get(InvestorAliasReview, review_id)
    if not review:
        raise DisclosurePipelineError("Alias review not found.")
    decision = decision.strip().upper()
    if decision not in {"APPROVED", "REJECTED"}:
        raise DisclosurePipelineError("decision must be APPROVED or REJECTED.")
    profiles = {item["id"] for item in load_investor_config()["investors"]}
    selected = investor_id or review.proposed_investor_id
    if decision == "APPROVED" and selected not in profiles:
        raise DisclosurePipelineError("The selected investor profile is not enabled.")
    review.proposed_investor_id = selected
    review.status = decision
    review.decision_note = note
    review.decided_at = datetime.utcnow()
    if decision == "APPROVED":
        document = db.get(DisclosureDocument, review.document_id)
        observation = ExtractedObservation(
            shareholder_name=review.observed_name,
            ownership_pct=review.ownership_pct, shares=review.shares,
        )
        _upsert_disclosure(db, document, observation, selected, f"{review.parser}_REVIEWED")
        document.matched_count += 1
        if not db.scalar(select(InvestorAliasReview.id).where(
            InvestorAliasReview.document_id == document.id,
            InvestorAliasReview.status == "PENDING",
        )):
            document.status = "PARSED"
    db.commit()
    db.refresh(review)
    deliver_notifications(db)
    return review


def deliver_notifications(db: Session) -> dict:
    pending = db.scalars(select(AppNotification).where(
        AppNotification.delivery_status.in_(["IN_APP", "FAILED"])
    ).order_by(AppNotification.created_at).limit(100)).all()
    if not settings.notification_webhook_url:
        return {"webhook_configured": False, "delivered": 0, "failed": 0}
    parsed = urlparse(settings.notification_webhook_url)
    if parsed.scheme != "https" or not parsed.hostname:
        return {"webhook_configured": True, "delivered": 0, "failed": len(pending),
                "error": "NOTIFICATION_WEBHOOK_URL must be HTTPS."}
    delivered = failed = 0
    with httpx.Client(timeout=15) as client:
        for item in pending:
            try:
                response = client.post(settings.notification_webhook_url, json={
                    "category": item.category, "severity": item.severity,
                    "title": item.title, "message": item.message, "payload": item.payload,
                    "created_at": item.created_at.isoformat(),
                })
                response.raise_for_status()
                item.delivery_status = "SENT"
                item.delivery_error = None
                delivered += 1
            except httpx.HTTPError as exc:
                item.delivery_status = "FAILED"
                item.delivery_error = str(exc)[:2000]
                failed += 1
    db.commit()
    return {"webhook_configured": True, "delivered": delivered, "failed": failed}


def disclosure_pipeline_status(db: Session) -> dict:
    document_counts = dict(db.execute(select(
        DisclosureDocument.status, func.count(DisclosureDocument.id)
    ).group_by(DisclosureDocument.status)).all())
    review_counts = dict(db.execute(select(
        InvestorAliasReview.status, func.count(InvestorAliasReview.id)
    ).group_by(InvestorAliasReview.status)).all())
    unread = db.scalar(select(func.count(AppNotification.id)).where(
        AppNotification.read_at.is_(None)
    )) or 0
    return {
        "enabled": settings.disclosure_auto_ingest_enabled,
        "parser_version": PARSER_VERSION,
        "documents": document_counts,
        "reviews": review_counts,
        "unread_notifications": unread,
        "mappings": db.scalar(select(func.count(DisclosureSourceMapping.id)).where(
            DisclosureSourceMapping.active.is_(True)
        )) or 0,
        "batch_size": settings.disclosure_batch_size,
        "llm": {
            "policy": settings.disclosure_llm_provider,
            "ollama_model": settings.ollama_model,
            "openrouter_configured": bool(settings.openrouter_api_key),
            "openrouter_model": settings.openrouter_model,
            "random_free_routing_allowed": False,
        },
        "notifications": {
            "in_app": True, "webhook_configured": bool(settings.notification_webhook_url),
        },
    }


def mapping_out(db: Session, mapping: DisclosureSourceMapping) -> dict:
    instrument = db.get(Instrument, mapping.instrument_id)
    return {
        "id": mapping.id, "instrument_id": mapping.instrument_id,
        "stock": f"{instrument.exchange}:{instrument.symbol}",
        "company_name": instrument.company_name,
        "exchange": mapping.exchange, "source_code": mapping.source_code,
        "active": mapping.active,
        "last_checked_at": mapping.last_checked_at.isoformat() if mapping.last_checked_at else None,
        "last_report_period": mapping.last_report_period.isoformat() if mapping.last_report_period else None,
        "last_status": mapping.last_status, "last_error": mapping.last_error,
    }


def review_out(db: Session, review: InvestorAliasReview) -> dict:
    instrument = db.get(Instrument, review.instrument_id)
    return {
        "id": review.id, "document_id": review.document_id,
        "stock": f"{instrument.exchange}:{instrument.symbol}",
        "company_name": instrument.company_name, "observed_name": review.observed_name,
        "proposed_investor_id": review.proposed_investor_id,
        "confidence": float(review.confidence), "report_date": review.report_date.isoformat(),
        "ownership_pct": float(review.ownership_pct),
        "shares": float(review.shares) if review.shares is not None else None,
        "source_url": review.source_url, "parser": review.parser,
        "status": review.status, "decision_note": review.decision_note,
    }


def notification_out(item: AppNotification) -> dict:
    return {
        "id": item.id, "category": item.category, "severity": item.severity,
        "title": item.title, "message": item.message, "payload": item.payload,
        "read": item.read_at is not None, "delivery_status": item.delivery_status,
        "delivery_error": item.delivery_error, "created_at": item.created_at.isoformat(),
    }
