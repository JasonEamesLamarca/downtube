"""Run Alembic migrations programmatically at startup."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config as AlembicConfig

from downtube.db import get_engine

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config() -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("sqlalchemy.url", str(get_engine().url))
    return cfg


def upgrade_db(revision: str = "head") -> None:
    cfg = alembic_config()
    with get_engine().begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, revision)
