from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Protocol


class ProviderDataError(ValueError):
    """Raised when provider data cannot be safely imported."""


@dataclass(frozen=True)
class MarketPrice:
    exchange: str
    symbol: str
    price_date: date
    close_price: Decimal
    source: str


@dataclass(frozen=True)
class CompanyResearch:
    exchange: str
    symbol: str
    company_name: str | None
    isin: str | None
    sector: str | None
    industry: str | None
    source: str


class MarketDataProvider(Protocol):
    name: str

    def get_eod_prices(
        self, symbols: list[str] | None = None, as_of: date | None = None
    ) -> list[MarketPrice]: ...


class CompanyResearchProvider(Protocol):
    name: str

    def get_company_research(
        self, symbols: list[str] | None = None
    ) -> list[CompanyResearch]: ...


def _csv_rows(content: bytes) -> list[dict[str, str]]:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ProviderDataError("CSV must use UTF-8 encoding.") from exc
    return list(csv.DictReader(io.StringIO(text)))


def _required(row: dict[str, str], field: str, row_number: int) -> str:
    value = (row.get(field) or "").strip()
    if not value:
        raise ProviderDataError(f"Row {row_number}: {field} is required.")
    return value


class CsvMarketDataProvider:
    name = "CSV"

    def __init__(self, content: bytes):
        self._content = content

    def get_eod_prices(
        self, symbols: list[str] | None = None, as_of: date | None = None
    ) -> list[MarketPrice]:
        wanted = {symbol.upper().strip() for symbol in symbols} if symbols else None
        prices: list[MarketPrice] = []
        for row_number, row in enumerate(_csv_rows(self._content), start=2):
            exchange = _required(row, "exchange", row_number).upper()
            symbol = _required(row, "symbol", row_number).upper()
            if wanted is not None and symbol not in wanted:
                continue
            try:
                price_date = date.fromisoformat(_required(row, "price_date", row_number))
                close_price = Decimal(_required(row, "price", row_number))
            except (ValueError, InvalidOperation) as exc:
                raise ProviderDataError(f"Row {row_number}: invalid date or price.") from exc
            if close_price <= 0:
                raise ProviderDataError(f"Row {row_number}: price must be greater than zero.")
            if as_of is not None and price_date > as_of:
                continue
            prices.append(MarketPrice(exchange, symbol, price_date, close_price, self.name))
        return prices


class CsvCompanyResearchProvider:
    name = "CSV_RESEARCH"

    def __init__(self, content: bytes):
        self._content = content

    def get_company_research(
        self, symbols: list[str] | None = None
    ) -> list[CompanyResearch]:
        wanted = {symbol.upper().strip() for symbol in symbols} if symbols else None
        results: list[CompanyResearch] = []
        for row_number, row in enumerate(_csv_rows(self._content), start=2):
            exchange = _required(row, "exchange", row_number).upper()
            symbol = _required(row, "symbol", row_number).upper()
            if wanted is not None and symbol not in wanted:
                continue
            optional = lambda field: (row.get(field) or "").strip() or None
            results.append(
                CompanyResearch(
                    exchange, symbol, optional("company_name"), optional("isin"),
                    optional("sector"), optional("industry"), self.name
                )
            )
        return results
