"""Sessions SQLAlchemy et initialisation explicite du schéma applicatif."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def create_database_engine(url: str) -> Engine:
    """Créer un moteur ; SQLite mémoire reste partageable dans le même processus."""
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    return create_engine(url, pool_pre_ping=True)


def create_session_factory(url: str) -> sessionmaker[Session]:
    """Construire une fabrique de sessions liée à une URL fournie par l'opérateur."""
    return sessionmaker(bind=create_database_engine(url), expire_on_commit=False)


def init_database(engine: Engine) -> None:
    """Créer les tables pour les tests ; la production utilise Alembic uniquement."""
    from .models import Base

    Base.metadata.create_all(engine)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Transaction courte : commit à la fin, rollback sur erreur, fermeture certaine."""
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def model_as_dict(value: Any) -> dict[str, Any]:
    """Convertir une ligne ORM en dictionnaire simple sans lazy loading."""
    return {column.name: getattr(value, column.name) for column in value.__table__.columns}
