"""Opt-in : le superviseur API et le moteur ZenML utilisent deux interpréteurs."""
import os
import threading
import time

import pytest
from tests.jobs.test_jobs import submit, URL

pytestmark = pytest.mark.skipif(not os.environ.get('SIGNALFLOW_TEST_ZENML_PYTHON'),
                               reason='runtime ZenML isolé non fourni')


def test_zenml_worker_success_and_cancellation(jobs):
    root, _, worker = jobs
    worker.orchestration = 'zenml'
    worker.zenml_python = os.environ['SIGNALFLOW_TEST_ZENML_PYTHON']
    identity = submit(root)['id']
    assert worker.tick()
    result = root.get(URL + '/' + identity).json()
    assert result['state'] == 'succeeded', result
    assert result['tracking_sync'] == 'synced'
    assert result['result']['orchestration']['execution_id']
    identity = submit(root, 'cancel-zenml')['id']
    thread = threading.Thread(target=worker.tick)
    thread.start()
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            row = root.get(URL + '/' + identity).json()
            attempt = row['attempt_id']
            if attempt and list((worker.root / attempt / 'output/orchestration').glob('*/execution.json')):
                break
            assert thread.is_alive(), row
            time.sleep(.02)
        else:
            pytest.fail('ZenML step did not start')
        assert root.post(URL + '/' + identity + '/cancel', json={}).status_code == 200
    finally:
        thread.join(timeout=60)
    assert not thread.is_alive()
    row = root.get(URL + '/' + identity).json()
    assert row['state'] == 'cancelled' and row['tracking_sync'] == 'synced', row
