"""Worker mono-exécution, processus enfants bornés et reprise sans réentraînement implicite."""
import argparse
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

from signalflow.persistence.session import create_session_factory
from ..datasets.storage import Storage
from ..settings import Settings
from .executor import atomic_json
from .queue import Queue


@dataclass(frozen=True)
class Limits:
    wall_seconds: float = 300
    cpu_seconds: int = 120
    memory_bytes: int = 2 * 1024**3
    file_bytes: int = 256 * 1024**2

    def __post_init__(self):
        if not (1 <= self.wall_seconds <= 86400 and 1 <= self.cpu_seconds <= 86400
                and 256 * 1024**2 <= self.memory_bytes <= 64 * 1024**3
                and 1024**2 <= self.file_bytes <= 4 * 1024**3):
            raise ValueError('invalid worker limits')

    def child(self):
        return {'cpu_seconds': self.cpu_seconds, 'memory_bytes': self.memory_bytes, 'file_bytes': self.file_bytes}


def environment(directory):
    import signalflow
    import signalflow_api
    # Les sources éditables peuvent être partagées, jamais tout le site-packages
    # du superviseur : cela écraserait les dépendances du runtime ZenML.
    roots = [Path(signalflow.__file__).parent.parent, Path(signalflow_api.__file__).parent.parent]
    sources = [str(root) for root in roots if not {'site-packages', 'dist-packages'}.intersection(root.parts)]
    # Aucun jeton, DSN, proxy, AWS ou credential MLflow hérité.
    return {'PATH': os.defpath, 'HOME': str(directory), 'TMPDIR': str(directory),
        'LANG': 'C.UTF-8', 'PYTHONPATH': os.pathsep.join(sources),
        'OPENBLAS_NUM_THREADS': '1', 'OMP_NUM_THREADS': '1', 'MKL_NUM_THREADS': '1',
        'MLFLOW_ENABLE_ASYNC_LOGGING': 'false', 'MLFLOW_ENABLE_TELEMETRY': 'false'}


def terminate(process):
    if process.poll() is None:
        # PID issu du Popen vivant uniquement ; aucun PID historique de la base.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait()


class Worker:
    def __init__(self, factory, root, dataset_root=None, limits=None, lease_seconds=30, orchestration="local", zenml_python=None):
        if orchestration not in {"local", "zenml"}:
            raise ValueError("invalid orchestration")
        if orchestration == "zenml" and not zenml_python:
            raise ValueError("isolated ZenML interpreter required")
        self.orchestration, self.zenml_python = orchestration, zenml_python
        self.root = Storage(root).root.resolve()
        self.datasets = Storage(dataset_root) if dataset_root else None
        self.queue = Queue(factory, lease_seconds)
        self.limits = limits or Limits()
        self.stop = threading.Event()
        self.lock_fd = None

    def prepare(self, job):
        directory = self.root / job.attempt_id
        directory.mkdir(mode=0o700)
        specification = dict(job.snapshot)
        expected = specification.pop('_dataset_sha256', None)
        if job.dataset_id:
            if self.datasets is None:
                raise RuntimeError('dataset storage not configured')
            digest = hashlib.sha256()
            size = 0
            with self.datasets.open(job.dataset_id) as source, (directory / 'dataset.csv').open('xb') as target:
                while chunk := source.read(65536):
                    size += len(chunk)
                    if size > 32 * 1024**2:
                        raise ValueError('dataset too large')
                    target.write(chunk)
                    digest.update(chunk)
                target.flush()
                os.fsync(target.fileno())
            if digest.hexdigest() != expected:
                raise ValueError('dataset checksum mismatch')
        atomic_json(directory / 'request.json', {'specification': specification, 'limits': self.limits.child(), 'orchestration': self.orchestration})
        return directory

    def child(self, directory, mode, job=None):
        backend = json.loads((directory / 'request.json').read_text()).get('orchestration', 'local')
        interpreter = self.zenml_python if backend == 'zenml' else sys.executable
        if not interpreter:
            raise RuntimeError('isolated ZenML interpreter required for recovery')
        child_env = environment(directory)
        if backend == 'zenml':
            child_env.update(ZENML_CONFIG_PATH=str(directory / 'zenml'),
                ZENML_ANALYTICS_OPT_IN='false', ZENML_ENABLE_RICH_TRACEBACK='false')
        # run_path évite d'ajouter jobs/ à sys.path (queue.py masquerait queue stdlib).
        bootstrap = "import runpy,sys; path=sys.argv.pop(1); runpy.run_path(path, run_name='__main__')"
        process = subprocess.Popen([interpreter, '-c', bootstrap, str(Path(__file__).with_name('executor.py')),
            str(directory), str(os.getpid()), mode], cwd=directory, env=child_env,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True, pass_fds=(self.lock_fd,))
        start = time.monotonic()
        try:
            while process.poll() is None:
                if self.stop.is_set():
                    return {'state': 'failed', 'error_code': 'WORKER_STOPPED'}
                if time.monotonic() - start > self.limits.wall_seconds:
                    return {'state': 'failed', 'error_code': 'WALL_TIME_LIMIT'}
                if job:
                    state = self.queue.heartbeat(job.id, job.owner)
                    if state is None:
                        return None  # Bail perdu : l’ancien propriétaire ne publie plus.
                    if state == 'cancel_requested':
                        return {'state': 'cancelled'}
                self.stop.wait(.25)
            if mode in ('succeeded', 'failed', 'cancelled'):
                return process.returncode == 0
            result = directory / 'result.json'
            if process.returncode == 0 and result.is_file():
                return json.loads(result.read_text())
            return {'state': 'failed', 'error_code': 'PROCESS_LIMIT_OR_CRASH'}
        finally:
            terminate(process)

    def dispatch(self):
        for identity, attempt, state in self.queue.pending():
            directory = self.root / attempt
            if not (directory / 'request.json').is_file():
                # Aucun moteur n’a démarré avant la publication de request.json.
                self.queue.delivered(identity)
                continue
            try:
                if self.child(directory, state) is True:
                    self.queue.delivered(identity)
            except Exception:
                # L’outbox reste durable pour le prochain passage.
                continue

    def tick(self):
        """Verrou OS partagé, hérité par les enfants même si le superviseur meurt."""
        fd = os.open(self.root / '.worker.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return False
            self.lock_fd = fd
            self.dispatch()
            claimed = self.queue.claim()
            if claimed is None:
                return False
            job, recovery = claimed
            try:
                if recovery:
                    directory = self.root / job.attempt_id
                    if (directory / 'request.json').is_file():
                        outcome = self.child(directory, 'recover', job)
                    else:
                        outcome = {'state': 'failed', 'error_code': 'WORKER_LOST'}
                else:
                    directory = self.prepare(job)
                    outcome = self.child(directory, 'execute', job)
            except Exception:
                outcome = {'state': 'failed', 'error_code': 'WORKER_ERROR'}
            if outcome:
                self.queue.finish(job.id, job.owner, outcome)
            self.dispatch()
            return True
        finally:
            self.lock_fd = None
            os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--allow-sqlite', action='store_true', help='développement mono-worker uniquement')
    args = parser.parse_args()
    settings = Settings.from_env()
    url = settings.database_url
    if not url or (not url.startswith('postgresql') and not args.allow_sqlite):
        parser.error('PostgreSQL requis (SQLite uniquement avec --allow-sqlite)')
    root = os.environ.get('SIGNALFLOW_JOB_ROOT')
    if not root:
        parser.error('SIGNALFLOW_JOB_ROOT requis')
    limits = Limits(wall_seconds=float(os.environ.get('SIGNALFLOW_JOB_WALL_SECONDS', 300)),
        cpu_seconds=int(os.environ.get('SIGNALFLOW_JOB_CPU_SECONDS', 120)),
        memory_bytes=int(os.environ.get('SIGNALFLOW_JOB_MEMORY_BYTES', 2 * 1024**3)))
    worker = Worker(create_session_factory(url), root, settings.dataset_root, limits,
        orchestration=os.environ.get("SIGNALFLOW_ORCHESTRATOR", "local"),
        zenml_python=os.environ.get("SIGNALFLOW_ZENML_PYTHON"))
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: worker.stop.set())
    while not worker.stop.is_set():
        try:
            worker.tick()
        except Exception:
            print('worker_tick_failed', file=sys.stderr)
        if args.once:
            break
        worker.stop.wait(1)


if __name__ == '__main__':
    main()
