import asyncio
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import create_async_engine

from cache_service.models import Base

MIGRATIONS = Path(__file__).parents[2] / "migrations"


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def schema_drift(connection: Connection) -> list[object]:
    return list(compare_metadata(MigrationContext.configure(connection), Base.metadata))


async def migrated_schema_drift(database_url: str) -> list[object]:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            return await connection.run_sync(schema_drift)
    finally:
        await engine.dispose()


async def drop_version_table(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    finally:
        await engine.dispose()


def test_migrations_match_the_models(database_url: str) -> None:
    # Sync test on purpose: Alembic's env.py runs its own event loop.
    config = alembic_config(database_url)
    try:
        command.upgrade(config, "head")
        assert asyncio.run(migrated_schema_drift(database_url)) == []

        command.downgrade(config, "base")
        command.upgrade(config, "head")
    finally:
        command.downgrade(config, "base")
        asyncio.run(drop_version_table(database_url))
