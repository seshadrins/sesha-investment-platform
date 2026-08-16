from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import Date, DateTime, Enum as SAEnum, ForeignKey, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class TransactionType(str, Enum):
    OPENING = "OPENING"
    BUY = "BUY"
    SELL = "SELL"
    DIVIDEND = "DIVIDEND"
    ADJUSTMENT_IN = "ADJUSTMENT_IN"
    ADJUSTMENT_OUT = "ADJUSTMENT_OUT"


class ThesisStatus(str, Enum):
    ACTIVE = "ACTIVE"
    WATCH = "WATCH"
    INVALID = "INVALID"


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    broker_name: Mapped[str] = mapped_column(String(120), default="Manual")
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="account")


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("exchange", "symbol", name="uq_instrument_exchange_symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    exchange: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(40))
    company_name: Mapped[str] = mapped_column(String(200))
    isin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(100), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    transactions: Mapped[list["Transaction"]] = relationship(back_populates="instrument")
    prices: Mapped[list["Price"]] = relationship(back_populates="instrument")


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    transaction_type: Mapped[TransactionType] = mapped_column(SAEnum(TransactionType))
    trade_date: Mapped[date] = mapped_column(Date)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6), default=0)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    charges: Mapped[Decimal] = mapped_column(Numeric(18, 4), default=0)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(30), default="MANUAL")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    account: Mapped[Account] = relationship(back_populates="transactions")
    instrument: Mapped[Instrument] = relationship(back_populates="transactions")


class Price(Base):
    __tablename__ = "prices"
    __table_args__ = (UniqueConstraint("instrument_id", "price_date", name="uq_price_instrument_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    price_date: Mapped[date] = mapped_column(Date)
    close_price: Mapped[Decimal] = mapped_column(Numeric(18, 4))
    source: Mapped[str] = mapped_column(String(30), default="MANUAL")

    instrument: Mapped[Instrument] = relationship(back_populates="prices")


class Thesis(Base):
    __tablename__ = "theses"
    __table_args__ = (UniqueConstraint("instrument_id", name="uq_current_thesis_instrument"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    status: Mapped[ThesisStatus] = mapped_column(SAEnum(ThesisStatus), default=ThesisStatus.ACTIVE)
    reason: Mapped[str] = mapped_column(Text, default="")
    catalysts: Mapped[str] = mapped_column(Text, default="")
    risks: Mapped[str] = mapped_column(Text, default="")
    invalidation_conditions: Mapped[str] = mapped_column(Text, default="")
    target_horizon_months: Mapped[int] = mapped_column(default=12)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class DecisionJournal(Base):
    __tablename__ = "decision_journal"

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    decision_date: Mapped[date] = mapped_column(Date, default=date.today)
    recommendation: Mapped[str] = mapped_column(String(30))
    rationale: Mapped[str] = mapped_column(Text)
    user_decision: Mapped[str | None] = mapped_column(String(30), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
