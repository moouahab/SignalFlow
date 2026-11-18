"""Environnement Alembic : l'URL vient uniquement de l'opérateur."""
from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from signalflow.persistence.models import Base

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    url = os.environ.get("SIGNALFLOW_DATABASE_URL", config.get_main_option("sqlalchemy.url", ""))
    if not url.strip():
        raise RuntimeError("SIGNALFLOW_DATABASE_URL est requis pour les migrations")
    return url


def run_migrations_offline() -> None:
    """Émettre du SQL sans connexion."""
    context.configure(url=_database_url(), target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Appliquer les migrations sur la base dédiée."""
    engine: Engine = create_engine(_database_url(), pool_pre_ping=True)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
