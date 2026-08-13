"""Run versioned business-database migrations for one institution.

The platform still contains legacy schema bootstrap functions.  During the
compatibility period those functions create the historical tables first, then
Alembic owns every new cross-domain schema change.  A PostgreSQL advisory lock
serializes startup when several API replicas start at the same time.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text


MIGRATION_LOCK_NAME = "longyun-business-schema-migrations-v1"


def _alembic_config() -> Config:
    app_directory = Path(__file__).resolve().parent
    config = Config()
    config.set_main_option("script_location", str(app_directory / "migrations"))
    return config


def run_business_schema_migrations(database_url: str) -> None:
    """Upgrade one institution business database to the current schema head."""
    if not database_url:
        raise RuntimeError("Institution migration database URL is required")
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.begin() as connection:
            connection.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_name))"),
                {"lock_name": MIGRATION_LOCK_NAME},
            )
            config = _alembic_config()
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
    finally:
        engine.dispose()


def current_schema_revision(database_url: str) -> str | None:
    """Return the installed Alembic revision without exposing credentials."""
    engine = create_engine(database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            return connection.scalar(text("SELECT version_num FROM alembic_version LIMIT 1"))
    finally:
        engine.dispose()
