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
