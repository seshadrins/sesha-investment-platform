from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum

from sqlalchemy import CheckConstraint, LargeBinary, Date, DateTime, Enum as SAEnum, ForeignKey, JSON, Numeric, String, Text, UniqueConstraint
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


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("instrument_id", name="uq_watchlist_instrument"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    source: Mapped[str] = mapped_column(String(60), default="MANUAL_STRONG_BUY")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class UniverseMembership(Base):
    __tablename__ = "universe_memberships"
    __table_args__ = (
        UniqueConstraint("universe_id", "instrument_id", name="uq_universe_instrument"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    universe_id: Mapped[str] = mapped_column(String(40))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    cap_segment: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(default=True)
    as_of: Mapped[date] = mapped_column(Date, default=date.today)
    source_url: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ScreeningResult(Base):
    __tablename__ = "screening_results"
    __table_args__ = (
        UniqueConstraint(
            "universe_id", "instrument_id", "screened_on", name="uq_screening_result_day"
        ),
        # Matches recommend_prospective()'s fixed outcome vocabulary exactly, so a bug or a
        # write that bypasses that function can't silently store a free-text value here.
        CheckConstraint(
            "recommendation IN ('STRONG_BUY','BUY','WATCH','AVOID','REVIEW')",
            name="ck_screening_result_recommendation_vocabulary",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    universe_id: Mapped[str] = mapped_column(String(40))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    screened_on: Mapped[date] = mapped_column(Date, default=date.today)
    recommendation: Mapped[str] = mapped_column(String(30))
    financial_score: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    style_matches: Mapped[int] = mapped_column(default=0)
    analysis_status: Mapped[str] = mapped_column(String(40))
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    criteria_version: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class InvestorDisclosure(Base):
    __tablename__ = "investor_disclosures"
    __table_args__ = (
        UniqueConstraint(
            "investor_id", "instrument_id", "report_date", name="uq_investor_disclosure_period"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    investor_id: Mapped[str] = mapped_column(String(60))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    report_date: Mapped[date] = mapped_column(Date)
    filed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ownership_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    shares: Mapped[Decimal | None] = mapped_column(Numeric(24, 2), nullable=True)
    source_url: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(40), default="EXCHANGE_FILING")
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class DisclosureSourceMapping(Base):
    __tablename__ = "disclosure_source_mappings"
    __table_args__ = (
        UniqueConstraint("exchange", "source_code", name="uq_disclosure_source_code"),
        UniqueConstraint("instrument_id", "exchange", name="uq_disclosure_instrument_exchange"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    exchange: Mapped[str] = mapped_column(String(10))
    source_code: Mapped[str] = mapped_column(String(40))
    active: Mapped[bool] = mapped_column(default=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_report_period: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    last_status: Mapped[str] = mapped_column(String(30), default="PENDING")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class DisclosureDocument(Base):
    __tablename__ = "disclosure_documents"
    __table_args__ = (UniqueConstraint("source_url", name="uq_disclosure_document_source"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    mapping_id: Mapped[int | None] = mapped_column(
        ForeignKey("disclosure_source_mappings.id"), nullable=True, index=True
    )
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    exchange: Mapped[str] = mapped_column(String(10))
    report_date: Mapped[date] = mapped_column(Date, index=True)
    filed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    source_url: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    parser: Mapped[str | None] = mapped_column(String(160), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="DISCOVERED", index=True)
    extracted_count: Mapped[int] = mapped_column(default=0)
    matched_count: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class InvestorAliasReview(Base):
    __tablename__ = "investor_alias_reviews"
    __table_args__ = (
        UniqueConstraint(
            "document_id", "observed_name", "proposed_investor_id",
            name="uq_alias_review_document_name_investor",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("disclosure_documents.id"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    observed_name: Mapped[str] = mapped_column(String(300))
    normalized_name: Mapped[str] = mapped_column(String(300), index=True)
    proposed_investor_id: Mapped[str] = mapped_column(String(60))
    confidence: Mapped[Decimal] = mapped_column(Numeric(6, 5))
    report_date: Mapped[date] = mapped_column(Date)
    filed_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    ownership_pct: Mapped[Decimal] = mapped_column(Numeric(10, 4))
    shares: Mapped[Decimal | None] = mapped_column(Numeric(24, 2), nullable=True)
    source_url: Mapped[str] = mapped_column(Text)
    parser: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="PENDING", index=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AppNotification(Base):
    __tablename__ = "app_notifications"
    __table_args__ = (UniqueConstraint("event_key", name="uq_notification_event"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    event_key: Mapped[str] = mapped_column(String(200))
    category: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(20), default="INFO")
    title: Mapped[str] = mapped_column(String(200))
    message: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivery_status: Mapped[str] = mapped_column(String(20), default="IN_APP")
    delivery_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)


class AnalysisSnapshot(Base):
    __tablename__ = "analysis_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_key: Mapped[str] = mapped_column(String(80), unique=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    last_attempted_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    last_status: Mapped[str] = mapped_column(String(20), default="PENDING")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AutomationRun(Base):
    __tablename__ = "automation_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_key: Mapped[str] = mapped_column(String(160), unique=True)
    scheduled_for: Mapped[datetime] = mapped_column(DateTime, index=True)
    trigger: Mapped[str] = mapped_column(String(30))
    attempt: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(20), default="RUNNING", index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    action_status: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class AutomationScheduleConfig(Base):
    __tablename__ = "automation_schedule_config"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    enabled: Mapped[bool] = mapped_column(default=True)
    days: Mapped[str] = mapped_column(String(40), default="tue-sat")
    hour: Mapped[int] = mapped_column(default=6)
    minute: Mapped[int] = mapped_column(default=0)
    timezone: Mapped[str] = mapped_column(String(80), default="Asia/Kolkata")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Transaction(Base):
    __tablename__ = "transactions"
    __table_args__ = (
        # A zero-quantity row is a no-op that calculate_position() silently skips and
        # stores forever with no warning; a negative row is never legitimate. Price and
        # charges stay >=0 (not >0) since a bonus allotment or split adjustment can
        # legitimately have zero cash value.
        CheckConstraint("quantity > 0", name="ck_transaction_quantity_positive"),
        CheckConstraint("price >= 0", name="ck_transaction_price_non_negative"),
        CheckConstraint("charges >= 0", name="ck_transaction_charges_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # RESTRICT (not CASCADE): no endpoint deletes an Account or Instrument today, but if
    # one is ever added, silently cascading away ledger history would be far worse than
    # a blocked delete.
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="RESTRICT"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="RESTRICT"), index=True)
    transaction_type: Mapped[TransactionType] = mapped_column(SAEnum(TransactionType))
    trade_date: Mapped[date] = mapped_column(Date, index=True)
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


class ResearchSnapshot(Base):
    __tablename__ = "research_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "provider", "research_type", name="uq_research_instrument_provider_type"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    provider: Mapped[str] = mapped_column(String(30))
    research_type: Mapped[str] = mapped_column(String(40))
    as_of: Mapped[date] = mapped_column(Date, default=date.today)
    payload: Mapped[dict] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ValuationMetricSnapshot(Base):
    __tablename__ = "valuation_metric_snapshots"
    __table_args__ = (
        UniqueConstraint("instrument_id", "metric", "as_of", "provider", name="uq_valuation_metric_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    metric: Mapped[str] = mapped_column(String(30))
    as_of: Mapped[date] = mapped_column(Date)
    value: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    sector_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    provider: Mapped[str] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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


class ThesisVersion(Base):
    __tablename__ = "thesis_versions"
    __table_args__ = (
        UniqueConstraint("instrument_id", "version", name="uq_thesis_instrument_version"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    version: Mapped[int] = mapped_column()
    status: Mapped[ThesisStatus] = mapped_column(SAEnum(ThesisStatus))
    reason: Mapped[str] = mapped_column(Text, default="")
    catalysts: Mapped[str] = mapped_column(Text, default="")
    risks: Mapped[str] = mapped_column(Text, default="")
    invalidation_conditions: Mapped[str] = mapped_column(Text, default="")
    target_horizon_months: Mapped[int] = mapped_column(default=12)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


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


class ResearchDocument(Base):
    __tablename__ = "research_documents"
    __table_args__ = (UniqueConstraint("instrument_id", "content_hash", name="uq_research_document_hash"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    document_type: Mapped[str] = mapped_column(String(30))
    title: Mapped[str] = mapped_column(String(240))
    report_date: Mapped[date] = mapped_column(Date)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    filename: Mapped[str] = mapped_column(String(240))
    content_type: Mapped[str] = mapped_column(String(100))
    content_hash: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    page_count: Mapped[int] = mapped_column(default=0)
    parser_version: Mapped[str] = mapped_column(String(40), default="document-text-v1")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ResearchDocumentSection(Base):
    __tablename__ = "research_document_sections"
    __table_args__ = (UniqueConstraint("document_id", "section_index", name="uq_document_section_index"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("research_documents.id"), index=True)
    section_index: Mapped[int] = mapped_column()
    page_number: Mapped[int | None] = mapped_column(nullable=True)
    heading: Mapped[str | None] = mapped_column(String(300), nullable=True)
    text: Mapped[str] = mapped_column(Text)


class GroundedDocumentAnalysis(Base):
    __tablename__ = "grounded_document_analyses"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("research_documents.id"), index=True)
    status: Mapped[str] = mapped_column(String(20), default="DRAFT", index=True)
    summary: Mapped[str] = mapped_column(Text)
    catalysts: Mapped[list] = mapped_column(JSON, default=list)
    risks: Mapped[list] = mapped_column(JSON, default=list)
    invalidation_conditions: Mapped[list] = mapped_column(JSON, default=list)
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(160))
    prompt_version: Mapped[str] = mapped_column(String(60))
    evidence_section_ids: Mapped[list] = mapped_column(JSON, default=list)
    omitted_section_count: Mapped[int] = mapped_column(default=0)
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class NotionalPortfolio(Base):
    __tablename__ = "notional_portfolios"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    starting_cash: Mapped[Decimal] = mapped_column(Numeric(20, 4))
    max_position_weight: Mapped[Decimal] = mapped_column(Numeric(8, 6), default=Decimal("0.15"))
    brokerage_pct: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal("0"))
    tax_pct: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal("0"))
    slippage_pct: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal("0"))
    reinvest_dividends: Mapped[bool] = mapped_column(default=False)
    benchmark_instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"), nullable=True)
    active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class NotionalTransaction(Base):
    __tablename__ = "notional_transactions"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("notional_portfolios.id"), index=True)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"), nullable=True, index=True)
    transaction_type: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(24), default="PENDING_PRICE", index=True)
    decision_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    target_price_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    execution_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(20, 6), default=0)
    requested_amount: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    execution_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4), nullable=True)
    charges: Mapped[Decimal] = mapped_column(Numeric(20, 4), default=0)
    recommendation_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    user_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class IPOIssue(Base):
    __tablename__ = "ipo_issues"
    __table_args__ = (UniqueConstraint("normalized_name", name="uq_ipo_normalized_name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    company_name: Mapped[str] = mapped_column(String(240))
    normalized_name: Mapped[str] = mapped_column(String(240), index=True)
    cin: Mapped[str | None] = mapped_column(String(30), nullable=True)
    isin: Mapped[str | None] = mapped_column(String(20), nullable=True)
    board: Mapped[str] = mapped_column(String(20), default="UNCLASSIFIED")
    stage: Mapped[str] = mapped_column(String(40), default="DISCOVERED", index=True)
    symbol: Mapped[str | None] = mapped_column(String(40), nullable=True)
    exchange: Mapped[str | None] = mapped_column(String(10), nullable=True)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"), nullable=True)
    discovered_on: Mapped[date] = mapped_column(Date)
    drhp_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    rhp_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    issue_open_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    issue_close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    listing_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    price_band_low: Mapped[Decimal | None] = mapped_column(Numeric(20,4), nullable=True)
    price_band_high: Mapped[Decimal | None] = mapped_column(Numeric(20,4), nullable=True)
    issue_price: Mapped[Decimal | None] = mapped_column(Numeric(20,4), nullable=True)
    lot_size: Mapped[int | None] = mapped_column(nullable=True)
    fresh_issue_amount: Mapped[Decimal | None] = mapped_column(Numeric(24,2), nullable=True)
    ofs_amount: Mapped[Decimal | None] = mapped_column(Numeric(24,2), nullable=True)
    source_url: Mapped[str] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(30), default="SEBI")
    last_checked_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class IPODocument(Base):
    __tablename__ = "ipo_documents"
    __table_args__ = (UniqueConstraint("ipo_id", "content_hash", name="uq_ipo_document_hash"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    ipo_id: Mapped[int] = mapped_column(ForeignKey("ipo_issues.id"), index=True)
    document_type: Mapped[str] = mapped_column(String(30))
    document_date: Mapped[date] = mapped_column(Date)
    title: Mapped[str] = mapped_column(String(300))
    source_url: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    page_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class IPODocumentSection(Base):
    __tablename__ = "ipo_document_sections"
    __table_args__ = (UniqueConstraint("document_id", "section_index", name="uq_ipo_section"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("ipo_documents.id"), index=True)
    section_index: Mapped[int] = mapped_column()
    page_number: Mapped[int | None] = mapped_column(nullable=True)
    heading: Mapped[str | None] = mapped_column(String(300), nullable=True)
    text: Mapped[str] = mapped_column(Text)


class IPOAnalysis(Base):
    __tablename__ = "ipo_analyses"
    id: Mapped[int] = mapped_column(primary_key=True)
    ipo_id: Mapped[int] = mapped_column(ForeignKey("ipo_issues.id"), index=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("ipo_documents.id"))
    outcome: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    payload: Mapped[dict] = mapped_column(JSON)
    provider: Mapped[str] = mapped_column(String(30))
    model: Mapped[str] = mapped_column(String(160))
    prompt_version: Mapped[str] = mapped_column(String(60), default="ipo-analysis-v1")
    review_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
