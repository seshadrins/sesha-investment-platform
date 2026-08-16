from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Instrument, Price
from .providers import CompanyResearchProvider, MarketDataProvider, ProviderDataError


def import_market_prices(db: Session, provider: MarketDataProvider) -> int:
    prices = provider.get_eod_prices()
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
    db.commit()
    return len(records)
