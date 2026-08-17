from datetime import date
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field

from .models import ThesisStatus, TransactionType


class AccountCreate(BaseModel):
    name: str
    broker_name: str = "Manual"
    currency: str = "INR"


class AccountOut(AccountCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)


class InstrumentCreate(BaseModel):
    exchange: str = "NSE"
    symbol: str
    company_name: str
    isin: str | None = None
    sector: str | None = None
    industry: str | None = None


class InstrumentOut(InstrumentCreate):
    id: int
    model_config = ConfigDict(from_attributes=True)


class TransactionCreate(BaseModel):
    account_id: int
    instrument_id: int
    transaction_type: TransactionType
    trade_date: date
    quantity: Decimal = Field(default=0, ge=0)
    price: Decimal = Field(default=0, ge=0)
    charges: Decimal = Field(default=0, ge=0)
    notes: str | None = None


class PriceCreate(BaseModel):
    instrument_id: int
    price_date: date
    close_price: Decimal = Field(gt=0)


class ThesisUpsert(BaseModel):
    instrument_id: int
    status: ThesisStatus = ThesisStatus.ACTIVE
    reason: str = ""
    catalysts: str = ""
    risks: str = ""
    invalidation_conditions: str = ""
    target_horizon_months: int = Field(default=12, ge=1, le=120)


class DecisionCreate(BaseModel):
    instrument_id: int
    decision_date: date = Field(default_factory=date.today)
    recommendation: str
    rationale: str
    user_decision: str | None = None
    notes: str | None = None


class UpstoxSyncRequest(BaseModel):
    as_of: date = Field(default_factory=date.today)
    include_prices: bool = True
    include_company_profiles: bool = True


class WatchlistCreate(BaseModel):
    instrument_id: int
    notes: str | None = None


class DisclosureSourceMappingUpsert(BaseModel):
    instrument_id: int
    exchange: str
    source_code: str
    active: bool = True


class DisclosureIngestionRequest(BaseModel):
    period: date | None = None
    force: bool = False
    mapping_ids: list[int] | None = None


class AliasReviewDecision(BaseModel):
    decision: str
    investor_id: str | None = None
    note: str | None = None


class DocumentAnalysisRequest(BaseModel):
    force: bool = False


class DocumentReviewDecision(BaseModel):
    decision: str
    note: str | None = None


class AutomationScheduleUpdate(BaseModel):
    enabled: bool = True
    days: str = Field(min_length=3, max_length=40)
    hour: int = Field(ge=0, le=23)
    minute: int = Field(ge=0, le=59)
    timezone: str = Field(min_length=1, max_length=80)


class NotionalPortfolioCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    starting_cash: Decimal = Field(gt=0)
    currency: str = Field(default="INR", min_length=3, max_length=8)
    max_position_weight: Decimal = Field(default=Decimal("0.15"), gt=0, le=1)
    brokerage_pct: Decimal = Field(default=Decimal("0"), ge=0, le=0.10)
    tax_pct: Decimal = Field(default=Decimal("0"), ge=0, le=0.50)
    slippage_pct: Decimal = Field(default=Decimal("0"), ge=0, le=0.10)
    reinvest_dividends: bool = False
    benchmark_instrument_id: int | None = None


class NotionalTradeCreate(BaseModel):
    instrument_id: int
    action: str
    quantity: Decimal | None = Field(default=None, gt=0)
    amount: Decimal | None = Field(default=None, gt=0)
    user_reason: str | None = Field(default=None, max_length=2000)


class NotionalCashCreate(BaseModel):
    action: str
    amount: Decimal = Field(gt=0)
    user_reason: str | None = Field(default=None, max_length=2000)
    instrument_id: int | None = None


class IPOIssueCreate(BaseModel):
    company_name: str = Field(min_length=2, max_length=240)
    board: str = "MAINBOARD"
    source_url: str
    discovered_on: date = Field(default_factory=date.today)
    cin: str | None = None
    isin: str | None = None


class IPOIssueUpdate(BaseModel):
    stage: str | None = None
    board: str | None = None
    symbol: str | None = None
    exchange: str | None = None
    instrument_id: int | None = None
    issue_open_date: date | None = None
    issue_close_date: date | None = None
    listing_date: date | None = None
    price_band_low: Decimal | None = Field(default=None, ge=0)
    price_band_high: Decimal | None = Field(default=None, ge=0)
    issue_price: Decimal | None = Field(default=None, ge=0)
    lot_size: int | None = Field(default=None, ge=1)
    fresh_issue_amount: Decimal | None = Field(default=None, ge=0)
    ofs_amount: Decimal | None = Field(default=None, ge=0)


class IPOAnalysisRequest(BaseModel):
    force: bool = False


class IPOAnalysisReview(BaseModel):
    decision: str
    note: str | None = None
