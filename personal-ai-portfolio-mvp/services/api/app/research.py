from sqlalchemy import select
from sqlalchemy.orm import Session

from datetime import date

from decimal import Decimal, InvalidOperation

from .models import Instrument, Price, ResearchSnapshot, ValuationMetricSnapshot
from .providers import CompanyResearchProvider, MarketDataProvider, ProviderDataError


def import_market_prices(db: Session, provider: MarketDataProvider, as_of: date | None = None) -> int:
    prices = provider.get_eod_prices(as_of=as_of)
    for quote in prices:
        instrument = db.scalar(select(Instrument).where(
            Instrument.exchange == quote.exchange, Instrument.symbol == quote.symbol
        ))
        if not instrument:
            raise ProviderDataError(f"Unknown instrument {quote.exchange}:{quote.symbol}")
        item = db.scalar(select(Price).where(
            Price.instrument_id == instrument.id, Price.price_date == quote.price_date
        ))
        if item:
            item.close_price, item.source = quote.close_price, quote.source
        else:
            db.add(Price(instrument_id=instrument.id, price_date=quote.price_date,
                         close_price=quote.close_price, source=quote.source))
    db.commit()
    return len(prices)


def import_company_research(db: Session, provider: CompanyResearchProvider) -> int:
    records = provider.get_company_research()
    for record in records:
        instrument = db.scalar(select(Instrument).where(
            Instrument.exchange == record.exchange, Instrument.symbol == record.symbol
        ))
        if not instrument:
            raise ProviderDataError(f"Unknown instrument {record.exchange}:{record.symbol}")
        for field in ("company_name", "isin", "sector", "industry"):
            value = getattr(record, field)
            if value is not None:
                setattr(instrument, field, value)
        if record.profile is not None:
            snapshot = db.scalar(select(ResearchSnapshot).where(
                ResearchSnapshot.instrument_id == instrument.id,
                ResearchSnapshot.provider == record.source,
                ResearchSnapshot.research_type == "COMPANY_PROFILE",
            ))
            payload = {"company_profile": record.profile, "sector": record.sector}
            if snapshot:
                snapshot.payload = payload
                snapshot.as_of = date.today()
            else:
                db.add(ResearchSnapshot(
                    instrument_id=instrument.id,
                    provider=record.source,
                    research_type="COMPANY_PROFILE",
                    as_of=date.today(),
                    payload=payload,
                ))
    db.commit()
    return len(records)


def store_fundamentals(db: Session, instrument: Instrument, bundle: dict, provider: str) -> int:
    today = date.today()
    for research_type, payload in bundle.items():
        snapshot = db.scalar(select(ResearchSnapshot).where(
            ResearchSnapshot.instrument_id == instrument.id,
            ResearchSnapshot.provider == provider,
            ResearchSnapshot.research_type == research_type,
        ))
        if snapshot:
            snapshot.payload, snapshot.as_of = payload, today
        else:
            db.add(ResearchSnapshot(instrument_id=instrument.id, provider=provider,
                research_type=research_type, as_of=today, payload=payload))

    for ratio in bundle.get("KEY_RATIOS", []):
        metric = str(ratio.get("name") or "").strip().upper()
        try:
            value = Decimal(str(ratio.get("company_value", "")).replace("%", "").replace(",", ""))
        except InvalidOperation:
            continue
        sector_raw = str(ratio.get("sector_value") or "").replace("%", "").replace(",", "")
        try:
            sector_value = Decimal(sector_raw) if sector_raw else None
        except InvalidOperation:
            sector_value = None
        item = db.scalar(select(ValuationMetricSnapshot).where(
            ValuationMetricSnapshot.instrument_id == instrument.id,
            ValuationMetricSnapshot.metric == metric,
            ValuationMetricSnapshot.as_of == today,
            ValuationMetricSnapshot.provider == provider,
        ))
        if item:
            item.value, item.sector_value = value, sector_value
        else:
            db.add(ValuationMetricSnapshot(instrument_id=instrument.id, metric=metric,
                as_of=today, value=value, sector_value=sector_value, provider=provider))
    db.commit()
    return sum(kind != "PROVIDER_ERRORS" for kind in bundle)
