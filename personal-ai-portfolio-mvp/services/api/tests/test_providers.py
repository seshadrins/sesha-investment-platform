from datetime import date
from decimal import Decimal

import pytest
import httpx

from app.providers import (
    CsvCompanyResearchProvider,
    CsvMarketDataProvider,
    ProviderDataError,
    ProviderInstrument,
    UpstoxClient,
    UpstoxCompanyResearchProvider,
    UpstoxMarketDataProvider,
)


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


def test_upstox_market_provider_selects_latest_eod_on_or_before_date():
    def handler(request: httpx.Request):
        assert request.headers["Authorization"] == "Bearer test-token"
        if request.url.path.endswith("/instruments/search"):
            return httpx.Response(200, json={"status": "success", "data": [{
                "exchange": "NSE", "isin": "INE000A00000", "instrument_key": "NSE_EQ|INE000A00000"
            }]})
        return httpx.Response(200, json={"status": "success", "data": {"candles": [
            ["2026-08-14T00:00:00+05:30", 1, 2, 1, 101.5, 10, 0],
            ["2026-08-13T00:00:00+05:30", 1, 2, 1, 99, 10, 0],
        ]}})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.upstox.com")
    client = UpstoxClient("test-token", client=http)
    provider = UpstoxMarketDataProvider(
        [ProviderInstrument("NSE", "ABC", "INE000A00000")], client
    )
    result = provider.get_eod_prices(as_of=date(2026, 8, 16))
    assert result[0].price_date == date(2026, 8, 14)
    assert result[0].close_price == Decimal("101.5")


def test_upstox_company_profile_provider():
    def handler(request: httpx.Request):
        assert request.url.path.endswith("/fundamentals/INE000A00000/profile")
        return httpx.Response(200, json={"status": "success", "data": {
            "company_profile": "Makes useful things.", "sector": "Industrials"
        }})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="https://api.upstox.com")
    provider = UpstoxCompanyResearchProvider(
        [ProviderInstrument("NSE", "ABC", "INE000A00000")], UpstoxClient("test-token", client=http)
    )
    result = provider.get_company_research()
    assert result[0].profile == "Makes useful things."
    assert result[0].sector == "Industrials"
