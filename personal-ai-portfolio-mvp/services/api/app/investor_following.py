from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Instrument, InvestorDisclosure


CONFIG_DIR = Path(__file__).with_name("followed_investors")


class InvestorDisclosureError(ValueError):
    pass


def classify_investor_signal(
    latest: Decimal, previous: Decimal | None, threshold: Decimal
) -> tuple[str, Decimal | None]:
    delta = latest - previous if previous is not None else None
    if latest == 0 and previous is not None and previous > 0:
        return "EXIT_REPORTED", delta
    if previous is None and latest > 0:
        return "NEW_DISCLOSURE", None
    if delta is not None and delta >= threshold:
        return "INCREASED", delta
    if delta is not None and delta <= -threshold:
        return "REDUCED", delta
    return "UNCHANGED", delta


def load_investor_config() -> dict[str, Any]:
    paths = sorted(CONFIG_DIR.glob("*.yaml"))
    if not paths:
        raise ValueError("No followed-investor configuration was found.")
    config = yaml.safe_load(paths[0].read_text(encoding="utf-8"))
    if not config.get("version") or not config.get("investors") or not config.get("methodology"):
        raise ValueError(f"{paths[0].name} is missing version, methodology, or investors.")
    ids = [item.get("id") for item in config["investors"]]
    if None in ids or len(ids) != len(set(ids)):
        raise ValueError("Followed-investor IDs must be present and unique.")
    config["file"] = paths[0].name
    config["investors"] = [item for item in config["investors"] if item.get("enabled", True)]
    return config


def import_investor_disclosures(db: Session, content: bytes) -> dict:
    config = load_investor_config()
    valid_ids = {item["id"] for item in config["investors"]}
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise InvestorDisclosureError("The disclosure CSV must be UTF-8 encoded.") from exc
    reader = csv.DictReader(io.StringIO(text))
    required = {
        "investor_id", "exchange", "symbol", "company_name", "report_date",
        "ownership_pct", "source_url",
    }
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise InvestorDisclosureError(
            "Required columns: " + ", ".join(sorted(required))
        )

    imported = updated = 0
    for line_number, row in enumerate(reader, start=2):
        investor_id = (row.get("investor_id") or "").strip()
        if investor_id not in valid_ids:
            raise InvestorDisclosureError(
                f"Line {line_number}: investor_id '{investor_id}' is not enabled in configuration."
            )
        exchange = (row.get("exchange") or "NSE").strip().upper()
        symbol = (row.get("symbol") or "").strip().upper()
        company_name = (row.get("company_name") or "").strip()
        source_url = (row.get("source_url") or "").strip()
        if exchange not in {"NSE", "BSE"} or not symbol or not company_name:
            raise InvestorDisclosureError(
                f"Line {line_number}: a valid NSE/BSE exchange, symbol, and company name are required."
            )
        if not source_url.startswith("https://"):
            raise InvestorDisclosureError(
                f"Line {line_number}: source_url must be an HTTPS link to the underlying disclosure."
            )
        try:
            report_date = date.fromisoformat((row.get("report_date") or "").strip())
            ownership_pct = Decimal((row.get("ownership_pct") or "").strip())
            shares_raw = (row.get("shares") or "").replace(",", "").strip()
            shares = Decimal(shares_raw) if shares_raw else None
            filed_raw = (row.get("filed_on") or "").strip()
            filed_on = date.fromisoformat(filed_raw) if filed_raw else None
        except (ValueError, InvalidOperation) as exc:
            raise InvestorDisclosureError(
                f"Line {line_number}: dates must be YYYY-MM-DD and numeric fields must be valid."
            ) from exc
        if not Decimal("0") <= ownership_pct <= Decimal("100"):
            raise InvestorDisclosureError(
                f"Line {line_number}: ownership_pct must be between 0 and 100."
            )
        if shares is not None and shares < 0:
            raise InvestorDisclosureError(f"Line {line_number}: shares cannot be negative.")

        instrument = db.scalar(select(Instrument).where(
            Instrument.exchange == exchange, Instrument.symbol == symbol
        ))
        if not instrument:
            instrument = Instrument(
                exchange=exchange, symbol=symbol, company_name=company_name,
                isin=(row.get("isin") or "").strip().upper() or None,
            )
            db.add(instrument)
            db.flush()
        disclosure = db.scalar(select(InvestorDisclosure).where(
            InvestorDisclosure.investor_id == investor_id,
            InvestorDisclosure.instrument_id == instrument.id,
            InvestorDisclosure.report_date == report_date,
        ))
        values = {
            "filed_on": filed_on, "ownership_pct": ownership_pct, "shares": shares,
            "source_url": source_url,
            "source_type": (row.get("source_type") or "EXCHANGE_FILING").strip().upper(),
        }
        if disclosure:
            for field, value in values.items():
                setattr(disclosure, field, value)
            updated += 1
        else:
            db.add(InvestorDisclosure(
                investor_id=investor_id, instrument_id=instrument.id,
                report_date=report_date, **values,
            ))
            imported += 1
    db.commit()
    return {"imported": imported, "updated": updated, "config_version": config["version"]}


def build_investor_signals(db: Session, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    config = load_investor_config()
    profiles = {item["id"]: item for item in config["investors"]}
    disclosures = db.scalars(select(InvestorDisclosure).where(
        InvestorDisclosure.investor_id.in_(list(profiles))
    ).order_by(InvestorDisclosure.report_date)).all()
    instrument_ids = {item.instrument_id for item in disclosures}
    instruments = {
        item.id: item for item in db.scalars(select(Instrument).where(
            Instrument.id.in_(instrument_ids)
        )).all()
    } if instrument_ids else {}
    grouped = defaultdict(list)
    for disclosure in disclosures:
        grouped[(disclosure.investor_id, disclosure.instrument_id)].append(disclosure)

    threshold = Decimal(str(config["methodology"]["material_change_percentage_points"]))
    stale_days = int(config["methodology"]["stale_after_days"])
    activity = []
    for (investor_id, instrument_id), history in grouped.items():
        latest = history[-1]
        previous = history[-2] if len(history) > 1 else None
        signal, delta = classify_investor_signal(
            latest.ownership_pct, previous.ownership_pct if previous else None, threshold
        )
        instrument = instruments[instrument_id]
        activity.append({
            "investor_id": investor_id,
            "investor_name": profiles[investor_id]["name"],
            "instrument_id": instrument.id,
            "exchange": instrument.exchange,
            "symbol": instrument.symbol,
            "company_name": instrument.company_name,
            "report_date": latest.report_date.isoformat(),
            "filed_on": latest.filed_on.isoformat() if latest.filed_on else None,
            "ownership_pct": float(latest.ownership_pct),
            "previous_ownership_pct": float(previous.ownership_pct) if previous else None,
            "change_percentage_points": float(delta) if delta is not None else None,
            "shares": float(latest.shares) if latest.shares is not None else None,
            "signal": signal,
            "stale": (as_of - latest.report_date).days > stale_days,
            "source_url": latest.source_url,
            "source_type": latest.source_type,
        })
    activity.sort(key=lambda item: (item["report_date"], item["company_name"]), reverse=True)

    rows = []
    for instrument_id in sorted(instrument_ids, key=lambda value: instruments[value].company_name):
        instrument = instruments[instrument_id]
        cells = {}
        for investor_id in profiles:
            item = next((entry for entry in activity if entry["instrument_id"] == instrument_id
                         and entry["investor_id"] == investor_id), None)
            cells[investor_id] = item
        rows.append({
            "instrument_id": instrument.id,
            "exchange": instrument.exchange,
            "symbol": instrument.symbol,
            "company_name": instrument.company_name,
            "investors": cells,
        })
    return {
        "config_version": config["version"],
        "config_file": config["file"],
        "methodology": config["methodology"],
        "profiles": list(profiles.values()),
        "rows": rows,
        "activity": activity,
        "limitations": [
            "Public shareholding disclosures are periodic and are not a live trade feed.",
            "A missing name is not proof of an exit; holdings below a disclosure threshold may disappear.",
            "Names and aliases can be ambiguous, and investment entities may require separate mapping.",
            "Investor activity is context only and does not change Phase 4 recommendations.",
        ],
    }
