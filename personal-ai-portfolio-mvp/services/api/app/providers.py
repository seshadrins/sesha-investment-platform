from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol
from urllib.parse import quote

import httpx


class ProviderDataError(ValueError):
    """Raised when provider data cannot be safely imported."""


class ProviderConnectionError(RuntimeError):
    """Raised when a remote provider cannot fulfil a request."""


@dataclass(frozen=True)
class ProviderInstrument:
    exchange: str
    symbol: str
    isin: str

    @property
    def upstox_key(self) -> str:
        segment = {"NSE": "NSE_EQ", "BSE": "BSE_EQ"}.get(self.exchange.upper())
        if not segment:
            raise ProviderDataError(f"Upstox equity adapter does not support {self.exchange}:{self.symbol}")
        return f"{segment}|{self.isin}"


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
    profile: str | None = None


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


class UpstoxClient:
    def __init__(
        self,
        token: str,
        base_url: str = "https://api.upstox.com",
        client: httpx.Client | None = None,
    ):
        if not token:
            raise ProviderDataError("UPSTOX_ANALYTICS_TOKEN is not configured.")
        self._owns_client = client is None
        self._client = client or httpx.Client(base_url=base_url, timeout=30)
        self._headers = {"Accept": "application/json", "Authorization": f"Bearer {token}"}

    def get(self, path: str, params: dict | None = None) -> Any:
        try:
            response = self._client.get(path, params=params, headers=self._headers)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            detail = f" (HTTP {status})" if status else ""
            message = None
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    error_body = exc.response.json()
                    errors = error_body.get("errors") or []
                    message = (errors[0].get("message") if errors else None) or error_body.get("message")
                except ValueError:
                    pass
            suffix = f" {message}" if message else ""
            raise ProviderConnectionError(f"Upstox request failed{detail}.{suffix}".strip()) from exc
        if body.get("status") != "success":
            raise ProviderConnectionError("Upstox returned an unsuccessful response.")
        return body.get("data", {})

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


class UpstoxMarketDataProvider:
    name = "UPSTOX"

    def __init__(self, instruments: list[ProviderInstrument], client: UpstoxClient):
        self._instruments = instruments
        self._client = client
        self.errors: list[str] = []

    def _resolve_key(self, instrument: ProviderInstrument) -> str | None:
        matches = self._client.get("/v2/instruments/search", params={
            "query": instrument.isin,
            "exchanges": instrument.exchange,
            "segments": "EQ",
            "records": 30,
        })
        exact = [item for item in matches if item.get("exchange") == instrument.exchange
                 and item.get("isin") == instrument.isin and item.get("instrument_key")]
        return exact[0]["instrument_key"] if exact else None

    def get_eod_prices(
        self, symbols: list[str] | None = None, as_of: date | None = None
    ) -> list[MarketPrice]:
        target_date = as_of or date.today()
        wanted = {symbol.upper().strip() for symbol in symbols} if symbols else None
        start_date = target_date - timedelta(days=10)
        prices: list[MarketPrice] = []
        for instrument in self._instruments:
            if wanted is not None and instrument.symbol.upper() not in wanted:
                continue
            try:
                resolved_key = self._resolve_key(instrument)
                if not resolved_key:
                    self.errors.append(
                        f"{instrument.exchange}:{instrument.symbol} — ISIN not found in the Upstox instrument master"
                    )
                    continue
                key = quote(resolved_key, safe="")
                data = self._client.get(
                    f"/v3/historical-candle/{key}/days/1/{target_date.isoformat()}/{start_date.isoformat()}"
                )
            except ProviderConnectionError as exc:
                self.errors.append(f"{instrument.exchange}:{instrument.symbol} — {exc}")
                continue
            candles = data.get("candles") or []
            eligible = []
            for candle in candles:
                try:
                    candle_date = date.fromisoformat(str(candle[0])[:10])
                    close_price = Decimal(str(candle[4]))
                except (IndexError, ValueError, InvalidOperation, TypeError) as exc:
                    raise ProviderDataError(f"Malformed Upstox candle for {instrument.symbol}.") from exc
                if candle_date <= target_date:
                    eligible.append((candle_date, close_price))
            if not eligible:
                self.errors.append(f"{instrument.exchange}:{instrument.symbol} — no EOD price found")
                continue
            price_date, close_price = max(eligible, key=lambda value: value[0])
            prices.append(MarketPrice(instrument.exchange, instrument.symbol, price_date, close_price, self.name))
        return prices


class UpstoxCompanyResearchProvider:
    name = "UPSTOX"

    def __init__(self, instruments: list[ProviderInstrument], client: UpstoxClient):
        self._instruments = instruments
        self._client = client
        self.errors: list[str] = []

    def get_company_research(
        self, symbols: list[str] | None = None
    ) -> list[CompanyResearch]:
        wanted = {symbol.upper().strip() for symbol in symbols} if symbols else None
        results = []
        for instrument in self._instruments:
            if wanted is not None and instrument.symbol.upper() not in wanted:
                continue
            try:
                data = self._client.get(f"/v2/fundamentals/{quote(instrument.isin, safe='')}/profile")
            except ProviderConnectionError as exc:
                self.errors.append(f"{instrument.exchange}:{instrument.symbol} — {exc}")
                continue
            results.append(
                CompanyResearch(
                    exchange=instrument.exchange,
                    symbol=instrument.symbol,
                    company_name=None,
                    isin=instrument.isin,
                    sector=data.get("sector"),
                    industry=None,
                    source=self.name,
                    profile=data.get("company_profile"),
                )
            )
        return results


class UpstoxFundamentalsProvider:
    """Fetch a complete, read-only fundamental evidence bundle for one company."""

    name = "UPSTOX"

    def __init__(self, client: UpstoxClient):
        self._client = client
        self.errors: list[str] = []

    def get_bundle(self, isin: str) -> dict[str, Any]:
        root = f"/v2/fundamentals/{quote(isin, safe='')}"
        requests = {
            "INCOME_ANNUAL": ("/income-statement", {
                "type": "consolidated", "time_period": "yearly", "fs": "true"
            }),
            "INCOME_QUARTERLY": ("/income-statement", {
                "type": "consolidated", "time_period": "quarterly"
            }),
            "BALANCE_SHEET": ("/balance-sheet", {"type": "consolidated", "fs": "true"}),
            "CASH_FLOW": ("/cash-flow", {"type": "consolidated", "fs": "true"}),
            "KEY_RATIOS": ("/key-ratios", None),
            "SHARE_HOLDINGS": ("/share-holdings", None),
            "CORPORATE_ACTIONS": ("/corporate-actions", None),
            "COMPETITORS": ("/competitors", None),
        }
        bundle = {}
        for kind, (path, params) in requests.items():
            try:
                bundle[kind] = self._client.get(root + path, params=params)
            except ProviderConnectionError as exc:
                self.errors.append(f"{kind} — {exc}")
        return bundle

    def search_instruments(self, query: str) -> list[dict]:
        data = self._client.get("/v2/instruments/search", params={
            "query": query,
            "exchanges": "NSE,BSE",
            "segments": "EQ",
            "records": 20,
        })
        return [{
            "exchange": item.get("exchange"),
            "symbol": item.get("trading_symbol"),
            "company_name": item.get("name") or item.get("short_name"),
            "isin": item.get("isin"),
            "instrument_key": item.get("instrument_key"),
            "instrument_type": item.get("instrument_type"),
        } for item in data if item.get("exchange") in {"NSE", "BSE"}
          and item.get("trading_symbol") and item.get("isin")]
