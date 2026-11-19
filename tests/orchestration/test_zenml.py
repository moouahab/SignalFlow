"""Tests opt-in dans le runtime ZenML isolé, avec de vraies étapes."""
import json
from pathlib import Path

import numpy as np
import pytest

DOCUMENT = {
    'training': {'schema_version': '0.0.1', 'seed': 42,
        'model': {'type': 'logistic_regression', 'parameters': {'C': 1., 'max_iter': 1000}}},
    'dataset': {'schema_version': '0.0.1', 'source': {'type': 'builtin', 'name': 'iris'},
        'split': {'validation': .2, 'test': .2, 'stratify': True}},
    'tracking': {'schema_version': '0.0.1', 'experiment_name': 'parity', 'run_name': 'iris'},
}


def test_pipeline_parity_and_no_cache(tmp_path):
    from signalflow.ml.contracts import ExecutionContext
    from signalflow.ml.runner import run_training_spec
    from signalflow.ml.validation import validate_training_spec
    from mlflow.tracking import MlflowClient
    from zenml.client import Client
    spec = validate_training_spec(DOCUMENT)
    local = run_training_spec(spec, ExecutionContext(tmp_path / 'local')).to_dict()
    results = []
    for name in ('zenml1', 'zenml2'):
        result = run_training_spec(spec, ExecutionContext(tmp_path / name, orchestration='zenml')).to_dict()
        assert result['metrics'] == local['metrics']
        assert json.loads((Path(result['artifacts']) / 'split_indices.json').read_text()) == json.loads(
            (Path(local['artifacts']) / 'split_indices.json').read_text())
        run = Client().get_pipeline_run(result['orchestration']['execution_id'])
        assert len(run.steps) == 6
        assert {step.status.value for step in run.steps.values()} == {'completed'}
        client = MlflowClient(tracking_uri=result['tracking_uri'])
        assert len(client.search_runs([client.get_experiment_by_name('parity').experiment_id])) == 1
        results.append(result)
    assert results[0]['orchestration']['execution_id'] != results[1]['orchestration']['execution_id']


def test_preprocessing_uses_only_train():
    from signalflow.ml.adapters.data import load_dataset, split_dataset
    from signalflow.orchestration.local import build_model, preprocess, fit
    raw = load_dataset({'type': 'builtin', 'name': 'iris'})
    split = split_dataset(raw, DOCUMENT['dataset']['split'], 42)
    model = build_model(DOCUMENT['training'])
    transformed = preprocess(model, raw, split)
    fit(model, transformed, raw, split)
    before = model['scaler'].mean_.copy()
    raw.X[split.test] = 1e12
    raw.X[split.validation] = -1e12
    other = build_model(DOCUMENT['training'])
    transformed = preprocess(other, raw, split)
    fit(other, transformed, raw, split)
    np.testing.assert_array_equal(other['scaler'].mean_, before)
    np.testing.assert_array_equal(other['classifier'].coef_, model['classifier'].coef_)


def test_failed_fit_is_not_retried_or_reported_success(tmp_path):
    from copy import deepcopy
    from signalflow.ml.contracts import ExecutionContext
    from signalflow.ml.runner import run_training_spec
    from signalflow.ml.validation import validate_training_spec
    from signalflow.ml.errors import PipelineError
    from mlflow.tracking import MlflowClient
    bad = deepcopy(DOCUMENT)
    bad['training']['model']['parameters']['max_iter'] = 1
    with pytest.raises(PipelineError):
        run_training_spec(validate_training_spec(bad), ExecutionContext(tmp_path, orchestration='zenml'))
    client = MlflowClient(tracking_uri=f'sqlite:///{tmp_path / "tracking/mlflow.db"}')
    runs = client.search_runs([client.get_experiment_by_name('parity').experiment_id])
    assert len(runs) == 1 and runs[0].info.status == 'FAILED'
    assert not list((tmp_path / 'orchestration').glob('*/model.joblib'))


def test_transient_load_retry(tmp_path, monkeypatch):
    from signalflow.orchestration import steps
    from signalflow.orchestration.zenml_adapter import ZenMLTrainer
    original = steps.load_dataset
    calls = []
    def transient(*args):
        calls.append(1)
        if len(calls) == 1:
            raise OSError('temporary read failure')
        return original(*args)
    monkeypatch.setattr(steps, 'load_dataset', transient)
    result = ZenMLTrainer(tmp_path).train(DOCUMENT['training'], DOCUMENT['dataset'])
    assert len(calls) == 2
    assert result.metrics['test_accuracy'] > .8
