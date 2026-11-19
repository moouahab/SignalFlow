"""Entrée enfant Linux : environnement réduit, limites, moteur Python puis résultat durable."""
import ctypes
import json
import math
import os
from pathlib import Path
import resource
import signal
import sys


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def restrict(parent, limits):
    if sys.platform != 'linux':
        raise RuntimeError('Linux required')
    # La mort du superviseur tue l’enfant ; vérifier aussi la course avant prctl.
    if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError('prctl failed')
    if os.getppid() != parent:
        raise RuntimeError('parent exited')
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (limits['cpu_seconds'], limits['cpu_seconds']))
    resource.setrlimit(resource.RLIMIT_AS, (limits['memory_bytes'], limits['memory_bytes']))
    resource.setrlimit(resource.RLIMIT_FSIZE, (limits['file_bytes'], limits['file_bytes']))


def safe_result(report):
    metrics = {key: value for key, value in report['metrics'].items()
               if isinstance(value, (float, int)) and math.isfinite(value)}
    result = {'run_id': report['run_id'], 'metrics': metrics}
    if report.get('model_package'):
        result['model_package'] = report['model_package']
    if report.get('orchestration'):
        result['orchestration'] = report['orchestration']
    return result


def tracking_runs(directory):
    database = directory / 'output' / 'tracking' / 'mlflow.db'
    if not database.exists():
        return None, []
    from mlflow.tracking import MlflowClient
    client = MlflowClient(tracking_uri=f'sqlite:///{database}')
    experiments = client.search_experiments()
    runs = client.search_runs([exp.experiment_id for exp in experiments], max_results=10) if experiments else []
    return client, runs


def recover(directory):
    """Un résultat moteur n’est réussi que si MLflow confirme FINISHED."""
    client, runs = tracking_runs(directory)
    if len(runs) == 1 and runs[0].info.status == 'FINISHED':
        for path in (directory / 'output' / 'runs').glob('*/report.json'):
            report = json.loads(path.read_text())
            if (report.get('status') == 'FINISHED' and report.get('run_id') == runs[0].info.run_id
                    and (path.parent / 'model.joblib').is_file()):
                return {'state': 'succeeded', 'result': safe_result(report)}
    return {'state': 'failed', 'error_code': 'WORKER_LOST'}


def synchronize(directory, state):
    client, runs = tracking_runs(directory)
    if state == 'succeeded':
        if len(runs) != 1 or runs[0].info.status != 'FINISHED':
            raise RuntimeError('tracking not finished')
    else:
        for run in runs:
            client.set_terminated(run.info.run_id, status='KILLED' if state == 'cancelled' else 'FAILED')


def execute(directory, specification, orchestration="local"):
    from signalflow.ml.contracts import ExecutionContext
    from signalflow.ml.runner import run_training_spec
    from signalflow.ml.validation import validate_training_spec
    report = run_training_spec(validate_training_spec(specification),
        ExecutionContext(output_dir=directory / 'output', dataset_root=directory, orchestration=orchestration)).to_dict()
    return {'state': 'succeeded', 'result': safe_result(report)}


def main():
    directory, parent, mode = Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
    request = json.loads((directory / 'request.json').read_text())
    restrict(parent, request['limits'])
    if mode in ('succeeded', 'failed', 'cancelled'):
        synchronize(directory, mode)
        if request.get('orchestration') == 'zenml' and (directory / 'zenml').exists():
            from signalflow.orchestration.zenml_adapter import synchronize as sync_zenml
            sync_zenml(mode, directory)
        return
    try:
        outcome = recover(directory) if mode == 'recover' else execute(directory, request['specification'], request.get('orchestration', 'local'))
    except Exception:
        # Aucun contenu de dataset, chemin, message de dépendance ou secret.
        outcome = {'state': 'failed', 'error_code': 'EXECUTION_FAILED'}
    atomic_json(directory / 'result.json', outcome)


if __name__ == '__main__':
    main()
