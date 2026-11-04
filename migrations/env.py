"""Alembic environment. Connection settings come from realestate Settings (.env / env vars)."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from realestate.infrastructure.config.settings import load_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Schema is written as raw SQL in versions/*.py; there is no SQLAlchemy model metadata.
target_metadata = None


def get_url() -> str:
    # Settings builds a libpq DSN; tell SQLAlchemy to use the psycopg (v3) driver we ship
    # instead of its default psycopg2.
    return load_settings().postgres_dsn.replace("postgresql://", "postgresql+psycopg://", 1)


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
