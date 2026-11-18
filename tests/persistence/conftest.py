"""Base SQLite isolée pour les tests de dépôt rapides."""
import pytest
from sqlalchemy.orm import sessionmaker

from signalflow.persistence.session import create_database_engine, init_database


@pytest.fixture
def factory(tmp_path):
    engine = create_database_engine(f'sqlite:///{tmp_path}/app.db')
    init_database(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()
