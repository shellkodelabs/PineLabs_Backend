"""
Alembic environment configuration.

The database URL is deliberately NOT stored in alembic.ini — it is loaded
from the same Settings object the application uses
(app.core.config.get_settings), which reads DATABASE_URL from the
environment / a local .env file. This keeps credentials out of version
control in exactly one place.

target_metadata is bound to app.core.database.Base.metadata. Importing
app.models below registers all 9 ORM models against that metadata, so
`alembic revision --autogenerate` (and Base.metadata.create_all() in
tests) sees the full schema.
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import get_settings
from app.core.database import Base

# Registers all ORM models against Base.metadata — required before
# autogenerate can see any tables.
import app.models  # noqa: F401,E402

config = context.config

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a live DB connection (emits SQL only)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live DB connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
