"""Six étapes ZenML réelles. Seules des références privées transitent entre étapes."""
from pathlib import Path
import json
import time
import warnings

import joblib
import numpy as np
from sklearn.exceptions import ConvergenceWarning
from threadpoolctl import threadpool_limits
from zenml import step, pipeline, get_step_context
from zenml.config import StepRetryConfig

from signalflow.ml.adapters.data import load_dataset, split_dataset
from .contracts import read_dataset, read_split, request, write_json
from .local import build_model, preprocess, fit, evaluate


@step(enable_cache=False, retry=StepRetryConfig(max_retries=1, delay=0, backoff=0))
def load(directory: str) -> str:
    settings = request(directory)
    write_json(Path(directory) / 'execution.json', {'backend': 'zenml',
        'execution_id': str(get_step_context().pipeline_run.id)})
    dataset = load_dataset(settings['dataset']['source'],
                           Path(settings['path']) if settings['path'] else None)
    np.savez(Path(directory) / 'dataset.npz', X=dataset.X, y=dataset.y,
             features=np.asarray(dataset.features), sha256=np.asarray(dataset.sha256))
    return directory


@step(enable_cache=False)
def split(loaded: str) -> str:
    settings = request(loaded)
    parts = split_dataset(read_dataset(loaded), settings['dataset']['split'], settings['training']['seed'])
    np.savez(Path(loaded) / 'split.npz', train=parts.train, validation=parts.validation, test=parts.test)
    return loaded


@step(enable_cache=False)
def preprocessing(partitioned: str) -> str:
    model = build_model(request(partitioned)['training'])
    start = time.perf_counter()
    with threadpool_limits(limits=1):
        transformed = preprocess(model, read_dataset(partitioned), read_split(partitioned))
    np.save(Path(partitioned) / 'train.npy', transformed, allow_pickle=False)
    joblib.dump(model, Path(partitioned) / 'preprocessed.joblib')
    write_json(Path(partitioned) / 'duration.json', time.perf_counter() - start)
    return partitioned


@step(enable_cache=False)
def training(preprocessed: str) -> str:
    model = joblib.load(Path(preprocessed) / 'preprocessed.joblib')
    start = time.perf_counter()
    with threadpool_limits(limits=1), warnings.catch_warnings():
        warnings.simplefilter('error', ConvergenceWarning)
        fit(model, np.load(Path(preprocessed) / 'train.npy', allow_pickle=False),
            read_dataset(preprocessed), read_split(preprocessed))
    duration = json.loads((Path(preprocessed) / 'duration.json').read_text()) + time.perf_counter() - start
    write_json(Path(preprocessed) / 'duration.json', duration)
    joblib.dump(model, Path(preprocessed) / 'fitted.joblib')
    return preprocessed


@step(enable_cache=False)
def evaluation(trained: str) -> str:
    model = joblib.load(Path(trained) / 'fitted.joblib')
    dataset, parts = read_dataset(trained), read_split(trained)
    with threadpool_limits(limits=1):
        metrics = {**evaluate(model, dataset, parts.validation, 'validation'),
                   **evaluate(model, dataset, parts.test, 'test')}
    write_json(Path(trained) / 'metrics.json', metrics)
    return trained


@step(enable_cache=False)
def save(evaluated: str) -> str:
    joblib.dump(joblib.load(Path(evaluated) / 'fitted.joblib'), Path(evaluated) / 'model.joblib')
    return evaluated


@pipeline(enable_cache=False)
def classification(directory: str):
    save(evaluation(training(preprocessing(split(load(directory))))))
