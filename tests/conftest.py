"""Les tests Compose sont opt-in ; la suite locale ne dépend d'aucun démon."""


def pytest_addoption(parser):
    parser.addoption('--compose', action='store_true', help='inclure les tests de la stack locale démarrée')


def pytest_ignore_collect(collection_path, config):
    return 'compose' in collection_path.parts and not config.getoption('--compose')
