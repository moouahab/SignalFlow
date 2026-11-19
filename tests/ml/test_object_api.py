"""Parité réelle YAML/objets et contraintes de la nouvelle frontière Python."""
import json
from pathlib import Path
import subprocess
import sys
import os
import threading

import pytest
import yaml

from signalflow.config.loader.errors import ConfigError, ConfigPathTraversalError, ConfigPathSymlinkError
from signalflow.ml.config import load_run_configuration
from signalflow.ml.contracts import ExecutionContext, TrainingSpec
from signalflow.ml.errors import PipelineError, TrainingError
from signalflow.ml.registry import create_registry
from signalflow.ml.runner import run_training, run_training_spec
from signalflow.ml.validation import validate_training_spec


@pytest.fixture
def spec_data(documents):
    return {"training": documents["training"], "dataset": documents["dataset"],
            "tracking": documents["mlops"]}


def test_spec_and_legacy_snapshot_do_not_share_nested_state(spec_data, ml_config):
    spec = validate_training_spec(spec_data)
    spec_data['training']['model']['parameters']['C'] = 12
    exported = spec.to_dict()
    exported['training']['model']['parameters']['C'] = 99
    assert spec.to_dict()['training']['model']['parameters']['C'] == 1
    loaded = load_run_configuration(ml_config, registry=create_registry())
    snapshot = loaded.snapshot()
    snapshot['training']['model']['parameters']['C'] = 99
    snapshot['project']['pipeline']['steps'].clear()
    assert loaded.training['model']['parameters']['C'] == 1
    assert loaded.loaded.project['pipeline']['steps']


@pytest.mark.parametrize('component,path,value', [
    ('training', ['seed'], True),
    ('training', ['model', 'parameters', 'C'], 0),
    ('training', ['model', 'parameters', 'C'], float('nan')),
    ('training', ['model', 'parameters', 'C'], float('inf')),
    ('training', ['model', 'parameters', 'max_iter'], 0),
    ('training', ['model', 'type'], 'os.system'),
    ('dataset', ['split', 'validation'], .9),
    ('dataset', ['source', 'name'], 'remote'),
    ('tracking', ['experiment_name'], ''),
    ('tracking', ['tracking_uri'], 'http://example.invalid'),
])
def test_yaml_and_object_validation_reject_same_inputs(spec_data, ml_config, component, path, value):
    target = spec_data[component]
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    filename = 'mlops' if component == 'tracking' else component
    (ml_config/f'{filename}.yaml').write_text(yaml.safe_dump(spec_data[component]))
    with pytest.raises(ConfigError) as python_error:
        validate_training_spec(spec_data)
    with pytest.raises(ConfigError) as yaml_error:
        load_run_configuration(ml_config, registry=create_registry())
    # Inf is rejected by the historical numeric schema, while the object API
    # rejects every non-finite JSON value before schema validation.
    if value != float('inf'):
        assert type(python_error.value) is type(yaml_error.value)


@pytest.mark.parametrize('document', [None, [], {}, {'training': object()}, {'training': {'x': {1,2}}}])
def test_invalid_envelopes_are_typed(document):
    with pytest.raises(ConfigError):
        validate_training_spec(document)


def test_forged_spec_is_revalidated_before_creating_output(spec_data, tmp_path):
    spec_data['training']['model']['parameters']['C'] = -1
    forged = TrainingSpec(json.dumps(spec_data))
    output = tmp_path/'not-created'
    with pytest.raises(ConfigError):
        run_training_spec(forged, ExecutionContext(output))
    assert not output.exists()


@pytest.mark.parametrize('escape', ['missing_root', 'traversal', 'symlink'])
def test_csv_root_is_required_and_confined(spec_data, tmp_path, escape):
    root = tmp_path/'data'
    root.mkdir()
    external = tmp_path/'outside.csv'
    external.write_text('x,y\n1,a\n2,b\n')
    (root/'link.csv').symlink_to(external)
    source = {'type': 'csv', 'path': './link.csv', 'target': 'y'}
    if escape == 'traversal':
        source['path'] = './../outside.csv'
    spec_data['dataset']['source'] = source
    errors = {'missing_root': PipelineError, 'traversal': ConfigPathTraversalError,
              'symlink': ConfigPathSymlinkError}
    context = ExecutionContext(tmp_path/'out', None if escape == 'missing_root' else root)
    with pytest.raises(errors[escape]):
        run_training_spec(validate_training_spec(spec_data), context)
    assert not (tmp_path/'out').exists()


def test_context_paths_do_not_change_after_chdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    context = ExecutionContext('out', 'data')
    child = tmp_path/'child'
    child.mkdir()
    monkeypatch.chdir(child)
    assert context.output_dir == tmp_path/'out'
    assert context.dataset_root == tmp_path/'data'


def test_object_validation_without_optional_sdks(spec_data, tmp_path):
    code = '''
import importlib.abc, json, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'sklearn','numpy','mlflow','psutil','prometheus_client','fastapi','pydantic'}:
            raise AssertionError('optional SDK imported during validation')
sys.meta_path.insert(0, Block())
from signalflow.ml.validation import validate_training_spec
from signalflow.ml.runner import run_training_spec
assert validate_training_spec(json.loads(sys.argv[1])).to_dict()['training']['seed']==42
'''
    subprocess.run([sys.executable,'-c',code,json.dumps(spec_data)], cwd=tmp_path,
                   env=dict(os.environ,PYTHONPATH=str(Path(__file__).resolve().parents[2]/'src')),
                   check=True, timeout=30)


def test_packaged_contracts_match_all_examples():
    from importlib.resources import files
    root = Path(__file__).resolve().parents[2]
    for name in ('training','dataset','mlops','monitoring'):
        filename = f'{name}.schema.json'
        embedded = json.loads(files('signalflow.config').joinpath('schemas',filename).read_text())
        for folder in ('config','examples/classification','examples/benchmark'):
            assert embedded == json.loads((root/folder/'schemas'/filename).read_text())


@pytest.mark.parametrize('source', ['builtin','csv','synthetic'])
def test_real_yaml_object_parity_without_yaml_reads(spec_data, ml_config, tmp_path, monkeypatch, source):
    np = pytest.importorskip('numpy')
    joblib = pytest.importorskip('joblib')
    pytest.importorskip('mlflow')
    if source == 'synthetic':
        spec_data['dataset']['source'] = {'type':'synthetic','n_samples':200,'n_features':8,
            'n_informative':4,'n_redundant':2,'n_classes':3,'random_state':123}
    elif source == 'csv':
        from sklearn.datasets import load_iris
        iris = load_iris()
        np.savetxt(ml_config/'iris.csv', np.column_stack((iris.data,iris.target)), delimiter=',',
                   header='a,b,c,d,target',comments='')
        spec_data['dataset']['source'] = {'type':'csv','path':'./iris.csv','target':'target'}
    (ml_config/'dataset.yaml').write_text(yaml.safe_dump(spec_data['dataset']))
    legacy = run_training(ml_config, output_dir=tmp_path/'yaml-run')
    spec = validate_training_spec(spec_data)
    # Prohibit every YAML loader, even though the test fixture has files for parity.
    from signalflow.config.loader import yaml_loader
    def forbidden(*args, **kwargs):
        raise AssertionError('object execution must not read YAML')
    monkeypatch.setattr(yaml_loader,'load_project',forbidden)
    object_report = run_training_spec(spec, ExecutionContext(tmp_path/'object-run',ml_config))
    result = object_report.to_dict()
    assert object_report.status == 'FINISHED'
    assert object_report.metrics == legacy['metrics']
    for filename in ('split_indices.json','dataset.json','metrics.json'):
        assert json.loads((Path(result['artifacts'])/filename).read_text()) == json.loads(
            (Path(legacy['artifacts'])/filename).read_text())
    left, right = joblib.load(legacy['model']), joblib.load(result['model'])
    np.testing.assert_array_equal(left[-1].coef_,right[-1].coef_)
    np.testing.assert_array_equal(left[-1].intercept_,right[-1].intercept_)
    result['metrics']['validation_accuracy'] = -1
    assert object_report.metrics['validation_accuracy'] >= 0
    from mlflow.tracking import MlflowClient
    tracked = MlflowClient(tracking_uri=result['tracking_uri']).get_run(object_report.run_id)
    assert tracked.info.status == 'FINISHED'
    assert tracked.data.metrics['validation_accuracy'] == object_report.metrics['validation_accuracy']
    # MLflow produit ses environnements YAML de sortie ; aucune configuration
    # YAML d'entrée n'est créée ni lue par l'API objets.
    assert all(path.parent.name == 'mlflow_model' and path.name in {'conda.yaml', 'python_env.yaml'}
               for path in (tmp_path/'object-run').rglob('*.yaml'))


@pytest.mark.parametrize('fail', [False,True])
def test_object_monitoring_publication_and_cleanup(spec_data, tmp_path, monkeypatch, fail):
    pytest.importorskip('mlflow')
    pytest.importorskip('psutil')
    from signalflow.observability.metrics import read_state
    monkeypatch.setenv('SIGNALFLOW_OBSERVABILITY_DIR',str(tmp_path/'telemetry'))
    spec_data['monitoring'] = {'schema_version':'0.0.1','interval_seconds':.05,'max_samples':100,
                              'prometheus':{'enabled':True},'logging':{'enabled':True}}
    if fail:
        spec_data['training']['model']['parameters']['max_iter'] = 1
        with pytest.raises(TrainingError):
            run_training_spec(validate_training_spec(spec_data),ExecutionContext(tmp_path/'out'))
    else:
        result = run_training_spec(validate_training_spec(spec_data),ExecutionContext(tmp_path/'out'))
        assert result.to_dict()['monitoring']['complete']
        assert result.to_dict()['observability']['warnings'] == []
    state = read_state(tmp_path/'telemetry/metrics.json')
    assert state['counts']['train']['failed' if fail else 'succeeded'] == 1
    assert not any(t.name=='signalflow-monitor' for t in threading.enumerate())
