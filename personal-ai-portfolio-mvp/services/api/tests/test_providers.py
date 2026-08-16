from datetime import date
from decimal import Decimal

import pytest

from app.providers import CsvCompanyResearchProvider, CsvMarketDataProvider, ProviderDataError


def test_csv_market_provider_normalises_and_filters():
    provider = CsvMarketDataProvider(
        b"exchange,symbol,price_date,price\n nse , abc ,2026-08-15,123.45\nNSE,XYZ,2026-08-17,10\n"
    )
    result = provider.get_eod_prices(as_of=date(2026, 8, 16))
    assert len(result) == 1
    assert result[0].symbol == "ABC"
    assert result[0].close_price == Decimal("123.45")


def test_csv_market_provider_rejects_non_positive_price():
    provider = CsvMarketDataProvider(b"exchange,symbol,price_date,price\nNSE,ABC,2026-08-15,0\n")
    with pytest.raises(ProviderDataError, match="greater than zero"):
        provider.get_eod_prices()


def test_csv_company_research_preserves_missing_optional_fields():
    provider = CsvCompanyResearchProvider(
        b"exchange,symbol,company_name,isin,sector,industry\nNSE,ABC,ABC Ltd,,Technology,\n"
    )
    result = provider.get_company_research()
    assert result[0].company_name == "ABC Ltd"
    assert result[0].isin is None
    assert result[0].sector == "Technology"
