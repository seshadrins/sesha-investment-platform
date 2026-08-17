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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
