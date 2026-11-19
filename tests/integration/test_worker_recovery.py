"""Arrêt brutal du superviseur : enfant tué et job réconcilié sans double fit."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from datetime import timedelta

from tests.jobs.conftest import jobs, clients
from tests.jobs.test_jobs import submit, URL
from signalflow.persistence.models import Job
from signalflow.persistence.session import session_scope
from signalflow_api.jobs.queue import now
from signalflow_api.jobs.worker import environment


def alive(pid):
    try:
        return Path(f'/proc/{pid}/stat').read_text().split()[2] != 'Z'
    except FileNotFoundError:
        return False


def test_supervisor_kill_cleans_child_and_recovers(jobs):
    root, _, worker = jobs
    identity = submit(root)['id']
    env = environment(worker.root)
    env.update(SIGNALFLOW_DATABASE_URL=root.app.state.settings.database_url,
               SIGNALFLOW_JOB_ROOT=str(worker.root), SIGNALFLOW_DATASET_ROOT=str(worker.datasets.root))
    supervisor = subprocess.Popen([sys.executable, '-m', 'signalflow_api.jobs.worker', '--once', '--allow-sqlite'],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    child = None
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            children = Path(f'/proc/{supervisor.pid}/task/{supervisor.pid}/children').read_text().split()
            if children:
                child = int(children[0])
                break
            assert supervisor.poll() is None
            time.sleep(.05)
        assert child is not None
        supervisor.kill()
        supervisor.wait(timeout=5)
        deadline = time.monotonic() + 10
        while alive(child) and time.monotonic() < deadline:
            time.sleep(.05)
        assert not alive(child)
    finally:
        if supervisor.poll() is None:
            supervisor.kill()
        supervisor.wait()
    with session_scope(worker.queue.factory) as session:
        session.get(Job, identity).lease_until = now() - timedelta(seconds=1)
    assert worker.tick()
    row = root.get(URL + '/' + identity).json()
    assert row['state'] == 'failed'
    assert worker.queue.claim() is None


def test_kernel_cpu_memory_and_file_limits(tmp_path):
    base = ('from signalflow_api.jobs.executor import restrict; import os; '
            'restrict(os.getppid(), {"cpu_seconds": 1, "memory_bytes": 268435456, "file_bytes": 1048576}); ')
    cpu = subprocess.run([sys.executable, '-c', base + '\nwhile True: pass'],
                         env=environment(tmp_path), timeout=10, capture_output=True)
    assert cpu.returncode in (-signal.SIGKILL, -signal.SIGXCPU)
    memory = subprocess.run([sys.executable, '-c', base + 'data = bytearray(512 * 1024**2)'],
                            env=environment(tmp_path), timeout=10, capture_output=True)
    assert memory.returncode != 0 and b'MemoryError' in memory.stderr
    disk = subprocess.run([sys.executable, '-c', base + 'open("large", "wb").write(b"x" * 2097152)'],
                          cwd=tmp_path, env=environment(tmp_path), timeout=10, capture_output=True)
    assert disk.returncode != 0
    assert (tmp_path / 'large').stat().st_size <= 1048576
