"""Les tests Docker/PostgreSQL sont opt-in ; la suite locale ne dépend d'aucun démon."""


def pytest_addoption(parser):
    parser.addoption('--zenml', action='store_true', help='tests du runtime ZenML isolé')
    parser.addoption('--compose', action='store_true', help='inclure les tests de la stack locale démarrée')
    parser.addoption('--postgres', action='store_true', help='inclure les tests PostgreSQL dédiés')


def pytest_ignore_collect(collection_path, config):
    parts = collection_path.parts
    if 'orchestration' in parts and not config.getoption('--zenml'):
        return True
    if 'compose' in parts and not config.getoption('--compose'):
        return True
    if 'postgres' in parts and not config.getoption('--postgres'):
        return True
    return False
