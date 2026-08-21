from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings

connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def apply_additive_migrations() -> None:
    """Apply small idempotent upgrades for installations without a migration runner."""
    if engine.dialect.name != "postgresql":
        return
    tables = set(inspect(engine).get_table_names())
    with engine.begin() as connection:
        if "disclosure_documents" in tables:
            connection.execute(text(
                "ALTER TABLE disclosure_documents ALTER COLUMN parser TYPE VARCHAR(160)"
            ))
        if "investor_alias_reviews" in tables:
            connection.execute(text(
                "ALTER TABLE investor_alias_reviews ALTER COLUMN parser TYPE VARCHAR(160)"
            ))
        if "disclosure_source_mappings" in tables:
            connection.execute(text(
                "ALTER TABLE disclosure_source_mappings ADD COLUMN IF NOT EXISTS "
                "last_report_period DATE"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_disclosure_source_mappings_last_report_period "
                "ON disclosure_source_mappings (last_report_period)"
            ))
        if "grounded_document_analyses" in tables:
            connection.execute(text(
                "ALTER TABLE grounded_document_analyses ADD COLUMN IF NOT EXISTS "
                "evidence_section_ids JSON NOT NULL DEFAULT '[]'"
            ))
            connection.execute(text(
                "ALTER TABLE grounded_document_analyses ADD COLUMN IF NOT EXISTS "
                "omitted_section_count INTEGER NOT NULL DEFAULT 0"
            ))
        if "notional_portfolios" in tables:
            connection.execute(text(
                "ALTER TABLE notional_portfolios ADD COLUMN IF NOT EXISTS "
                "tax_pct NUMERIC(10,6) NOT NULL DEFAULT 0"
            ))
            connection.execute(text(
                "ALTER TABLE notional_portfolios ADD COLUMN IF NOT EXISTS "
                "reinvest_dividends BOOLEAN NOT NULL DEFAULT FALSE"
            ))
        if "transactions" in tables:
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_transactions_account_id "
                "ON transactions (account_id)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_transactions_instrument_id "
                "ON transactions (instrument_id)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_transactions_trade_date "
                "ON transactions (trade_date)"
            ))
            _add_check_constraint_if_missing(
                connection, "transactions", "ck_transaction_quantity_positive",
                "CHECK (quantity > 0)",
            )
            _add_check_constraint_if_missing(
                connection, "transactions", "ck_transaction_price_non_negative",
                "CHECK (price >= 0)",
            )
            _add_check_constraint_if_missing(
                connection, "transactions", "ck_transaction_charges_non_negative",
                "CHECK (charges >= 0)",
            )
        if "screening_results" in tables:
            _add_check_constraint_if_missing(
                connection, "screening_results", "ck_screening_result_recommendation_vocabulary",
                "CHECK (recommendation IN ('STRONG_BUY','BUY','WATCH','AVOID','REVIEW'))",
            )


def _add_check_constraint_if_missing(
    connection, table_name: str, constraint_name: str, constraint_sql: str
) -> None:
    """Postgres has no `ADD CONSTRAINT IF NOT EXISTS`, so guard with an explicit lookup.
    A pre-existing row that violates the new constraint would make this ALTER fail loudly
    at startup rather than silently skip — that's intentional: it surfaces bad data instead
    of pretending the constraint is enforced when it isn't."""
    exists = connection.execute(text(
        "SELECT 1 FROM information_schema.table_constraints "
        "WHERE table_name = :table_name AND constraint_name = :constraint_name"
    ), {"table_name": table_name, "constraint_name": constraint_name}).first()
    if not exists:
        connection.execute(text(
            f"ALTER TABLE {table_name} ADD CONSTRAINT {constraint_name} {constraint_sql}"
        ))


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
