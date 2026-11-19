import pytest
from tests.api.test_project_isolation import clients, DOCUMENT
from signalflow_api.jobs.worker import Worker


@pytest.fixture
def jobs(clients, tmp_path):
    root = clients('root')
    root.post('/api/v1/projects', json={'key': 'jobs', 'name': 'Jobs'})
    created = root.post('/api/v1/projects/jobs/configurations', json={'configuration': DOCUMENT})
    assert created.status_code == 201, created.text
    factory = root.app.state.session_factory
    worker = Worker(factory, tmp_path / 'jobs', tmp_path / 'datasets')
    return root, clients, worker
