"""Adaptateur local : copies vérifiées sans désérialisation, versions et alias MLflow."""
import json
from pathlib import Path
import re
import shutil
import tempfile

from signalflow.ml.model_package import digest
from ..errors import ApiError


def checked_file(path, root):
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ApiError(409, 'MODEL_UNTRUSTED', 'Artefacts du modèle non fiables.')
    for parent in path.parents:
        if parent == root:
            break
        if parent.is_symlink():
            raise ApiError(409, 'MODEL_UNTRUSTED', 'Artefacts du modèle non fiables.')
    return path


def source_package(settings, job):
    manifest = (job.result or {}).get('model_package')
    if not manifest or manifest.get('format') != 1:
        raise ApiError(409, 'MODEL_PACKAGE_REQUIRED', 'Run historique sans package MLflow ; nouvel entraînement requis.')
    if not settings.job_root:
        raise ApiError(503, 'JOB_STORAGE_NOT_CONFIGURED', 'Stockage des runs non configuré.')
    root = Path(settings.job_root).resolve()
    if not re.fullmatch('[a-f0-9]{32}', job.attempt_id or ''):
        raise ApiError(409, 'MODEL_UNTRUSTED', 'Tentative invalide.')
    directory = root / job.attempt_id / 'output'
    candidates = []
    for report in (directory / 'runs').glob('*/report.json'):
        value = json.loads(checked_file(report, root).read_text())
        if value.get('run_id') == job.result['run_id'] and value.get('status') == 'FINISHED':
            candidates.append(report.parent / 'mlflow_model')
    if len(candidates) != 1:
        raise ApiError(409, 'MODEL_UNTRUSTED', 'Package unique du run introuvable.')
    package = candidates[0]
    verify_package(package, manifest, root)
    from mlflow.tracking import MlflowClient
    database = checked_file(directory / 'tracking' / 'mlflow.db', root)
    run = MlflowClient(tracking_uri=f'sqlite:///{database}').get_run(job.result['run_id'])
    if run.info.status != 'FINISHED':
        raise ApiError(409, 'RUN_NOT_FINISHED', 'Le tracking ne confirme pas le succès.')
    return package, manifest


def verify_package(package, manifest, root):
    files = manifest.get('files', {})
    required = {'MLmodel', 'model.pkl', 'requirements.txt', 'conda.yaml', 'python_env.yaml', 'provenance.json'}
    if set(files) != required or {p.name for p in package.iterdir()} != required:
        raise ApiError(409, 'MODEL_UNTRUSTED', 'Contenu du package invalide.')
    for name, checksum in files.items():
        if digest(checked_file(package / name, root)) != checksum:
            raise ApiError(409, 'MODEL_UNTRUSTED', 'Empreinte du modèle incorrecte.')
    import yaml
    metadata = yaml.safe_load((package / 'MLmodel').read_text())
    if not metadata.get('signature') or not {'sklearn', 'python_function'} <= metadata.get('flavors', {}).keys():
        raise ApiError(409, 'MODEL_UNTRUSTED', 'Signature ou flavor manquant.')


class MLflowRegistry:
    def __init__(self, root):
        from mlflow.tracking import MlflowClient
        self.root = root
        uri = f'sqlite:///{root / "registry.db"}'
        self.client = MlflowClient(tracking_uri=uri, registry_uri=uri)

    @staticmethod
    def name(project_id, name):
        return f'sf_{project_id}_{name}'

    def versions(self, name):
        # Nom interne construit avec uniquement caractères alphanumériques/_/-.
        token, values = None, []
        while True:
            page = self.client.search_model_versions(f"name = '{name}'", max_results=1000, page_token=token)
            values.extend(page)
            token = page.token
            if not token:
                return values

    def register(self, name, registration, job, package, manifest):
        existing = [v for v in self.versions(name) if v.tags.get('signalflow.registration_id') == registration.id]
        if existing:
            if len(existing) != 1:
                raise ApiError(409, 'REGISTRY_CONFLICT', 'Plusieurs versions pour cette promotion.')
            return existing[0]
        from mlflow.exceptions import MlflowException
        try:
            self.client.create_registered_model(name)
        except MlflowException as error:
            if error.error_code != 'RESOURCE_ALREADY_EXISTS':
                raise
        target = self.root / 'packages' / registration.id
        target.parent.mkdir(mode=0o700, exist_ok=True)
        if not target.exists():
            with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
                staged = Path(temporary) / 'model'
                shutil.copytree(package, staged)
                verify_package(staged, manifest, self.root)
                staged.rename(target)
        verify_package(target, manifest, self.root)
        return self.client.create_model_version(name, target.as_uri(), run_id=job.result['run_id'],
            tags={'signalflow.registration_id': registration.id, 'signalflow.job_id': job.id,
                  'signalflow.author_id': registration.author_id,
                  **{f'signalflow.{key}': value for key, value in manifest['provenance'].items()}},
            description=registration.reason, await_creation_for=0)

    def alias(self, name, alias):
        from mlflow.exceptions import MlflowException
        try:
            value = self.client.get_registered_model(name).aliases.get(alias)
            return str(value) if value is not None else None
        except MlflowException as error:
            if error.error_code == 'RESOURCE_DOES_NOT_EXIST':
                return None
            raise

    def public(self, model):
        return {'version': str(model.version), 'uri': f'models:/{model.name}/{model.version}',
                'run_id': model.run_id, 'status': model.status,
                'job_id': model.tags.get('signalflow.job_id'),
                'author_id': model.tags.get('signalflow.author_id'),
                'reason': model.description,
                'provenance': {key.removeprefix('signalflow.'): value for key, value in model.tags.items()
                               if key.startswith('signalflow.') and key.endswith('_sha256')}}
