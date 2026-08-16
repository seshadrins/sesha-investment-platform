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


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
