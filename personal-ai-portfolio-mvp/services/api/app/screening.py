from __future__ import annotations

import csv
import io
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Instrument, UniverseMembership


UNIVERSE_DIR = Path(__file__).with_name("screening_universes")


class ScreeningSourceError(RuntimeError):
    pass


def load_screening_universes() -> list[dict[str, Any]]:
    universes = []
    for path in sorted(UNIVERSE_DIR.glob("*.yaml")):
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
        required = {"id", "name", "description", "shortlist_recommendation", "sources"}
        missing = required - set(config or {})
        if missing:
            raise ValueError(f"{path.name} is missing: {', '.join(sorted(missing))}")
        config["file"] = path.name
        universes.append(config)
    return universes


def get_screening_universe(universe_id: str) -> dict[str, Any]:
    try:
        return next(item for item in load_screening_universes() if item["id"] == universe_id)
    except StopIteration as exc:
        raise KeyError(universe_id) from exc


def quarter_start(value: date) -> date:
    """Return the first day of the calendar quarter containing ``value``."""
    return date(value.year, ((value.month - 1) // 3) * 3 + 1, 1)


def select_balanced_memberships(memberships: list, batch_size: int) -> list:
    """Select a deterministic Large/Mid/Small-cap-balanced screening batch."""
    by_segment = {
        segment: [item for item in memberships if item.cap_segment == segment]
        for segment in ("LARGE", "MID", "SMALL")
    }
    selected = []
    while len(selected) < batch_size and any(by_segment.values()):
        for segment in ("LARGE", "MID", "SMALL"):
            if by_segment[segment] and len(selected) < batch_size:
                selected.append(by_segment[segment].pop(0))
    return selected


def parse_constituent_csv(content: bytes, cap_segment: str, source_url: str) -> list[dict]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    reader = csv.DictReader(io.StringIO(text))
    required = {"Company Name", "Industry", "Symbol", "ISIN Code"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ScreeningSourceError(
            "The NSE Indices constituent file did not contain the expected columns."
        )
    rows = []
    for row in reader:
        symbol = (row.get("Symbol") or "").strip().upper()
        isin = (row.get("ISIN Code") or "").strip().upper()
        if not symbol or not isin:
            continue
        rows.append({
            "exchange": "NSE",
            "symbol": symbol,
            "company_name": (row.get("Company Name") or symbol).strip(),
            "isin": isin,
            "industry": (row.get("Industry") or "").strip() or None,
            "cap_segment": cap_segment,
            "source_url": source_url,
        })
    return rows


def fetch_universe_constituents(
    config: dict[str, Any], client: httpx.Client | None = None
) -> list[dict]:
    owns_client = client is None
    client = client or httpx.Client(
        follow_redirects=True,
        timeout=30,
        headers={"User-Agent": "PersonalPortfolioResearch/1.0"},
    )
    combined: dict[str, dict] = {}
    try:
        for source in config["sources"]:
            try:
                response = client.get(source["url"])
                response.raise_for_status()
            except httpx.HTTPError as exc:
                raise ScreeningSourceError(
                    f"Could not download {source['name']} constituents: {exc}"
                ) from exc
            for row in parse_constituent_csv(
                response.content, source["cap_segment"], source["url"]
            ):
                if row["isin"] in combined:
                    raise ScreeningSourceError(
                        f"Duplicate constituent {row['isin']} appeared across cap segments."
                    )
                combined[row["isin"]] = row
    finally:
        if owns_client:
            client.close()
    return list(combined.values())


def refresh_universe_memberships(
    db: Session, config: dict[str, Any], constituents: list[dict], as_of: date | None = None
) -> dict:
    as_of = as_of or date.today()
    existing_memberships = {
        item.instrument_id: item for item in db.scalars(select(UniverseMembership).where(
            UniverseMembership.universe_id == config["id"]
        )).all()
    }
    for membership in existing_memberships.values():
        membership.active = False

    counts = {"LARGE": 0, "MID": 0, "SMALL": 0}
    for row in constituents:
        instrument = db.scalar(select(Instrument).where(
            Instrument.exchange == row["exchange"], Instrument.symbol == row["symbol"]
        ))
        if not instrument:
            instrument = Instrument(
                exchange=row["exchange"], symbol=row["symbol"],
                company_name=row["company_name"], isin=row["isin"],
                sector=row["industry"], industry=row["industry"],
            )
            db.add(instrument)
            db.flush()
        else:
            instrument.company_name = row["company_name"]
            instrument.isin = row["isin"]
            if row["industry"]:
                instrument.sector = row["industry"]
                instrument.industry = row["industry"]

        membership = existing_memberships.get(instrument.id)
        if membership:
            membership.cap_segment = row["cap_segment"]
            membership.active = True
            membership.as_of = as_of
            membership.source_url = row["source_url"]
        else:
            db.add(UniverseMembership(
                universe_id=config["id"], instrument_id=instrument.id,
                cap_segment=row["cap_segment"], active=True, as_of=as_of,
                source_url=row["source_url"],
            ))
        counts[row["cap_segment"]] = counts.get(row["cap_segment"], 0) + 1
    db.commit()
    return {"constituents": len(constituents), "segments": counts, "as_of": as_of.isoformat()}
