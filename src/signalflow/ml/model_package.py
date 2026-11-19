"""Package MLflow créé depuis le modèle en mémoire, jamais depuis un pickle entrant."""
import hashlib
import json
from importlib.metadata import version


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(65536), b''):
            checksum.update(chunk)
    return checksum.hexdigest()


def save_package(result, destination):
    from mlflow.models import infer_signature
    from mlflow.sklearn import save_model

    # Signature déduite du train uniquement ; aucun exemple de données publié.
    sample = result.dataset.X[result.split.train[:5]]
    signature = infer_signature(sample, result.model.predict(sample))
    package = destination / 'mlflow_model'
    save_model(result.model, str(package), signature=signature, serialization_format='cloudpickle',
               pip_requirements=[f'{name}=={version(name)}' for name in
                                 ('scikit-learn', 'numpy', 'scipy', 'pandas', 'cloudpickle')])
    provenance = {
        'dataset_sha256': result.dataset.sha256,
        'split_sha256': digest(destination / 'split_indices.json'),
        'environment_sha256': digest(destination / 'environment.json'),
        'configuration_sha256': digest(destination / 'configuration.json'),
    }
    (package / 'provenance.json').write_text(json.dumps(provenance, sort_keys=True))
    files = {path.name: digest(path) for path in sorted(package.iterdir()) if path.is_file()}
    manifest = {'format': 1, 'files': files, 'provenance': provenance}
    (destination / 'model_manifest.json').write_text(json.dumps(manifest, sort_keys=True))
    return manifest
