"""Entraînement et transformations réels, sans services distants."""

from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
pytest.importorskip("sklearn")

from signalflow.ml.adapters.data import Dataset, load_dataset, split_dataset
from signalflow.ml.adapters.sklearn import SklearnTrainer
from signalflow.ml.errors import DatasetError, TrainingError


@pytest.mark.parametrize("name,shape", [("iris", (150, 4)), ("wine", (178, 13)), ("breast_cancer", (569, 30))])
def test_builtin_datasets(name, shape):
    dataset = load_dataset({"type": "builtin", "name": name})
    assert dataset.X.shape == shape
    assert len(dataset.sha256) == 64
    assert load_dataset({"type": "builtin", "name": name}).sha256 == dataset.sha256


def test_csv_numeric_features_and_missing_values(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("x,target,z\n1,a,2\n,b,4\n3,a,6\n4,b,8\n")
    data = load_dataset({"type": "csv", "target": "target"}, path)
    assert data.features == ("x", "z")
    assert data.X.shape == (4, 2)
    assert np.isnan(data.X[1, 0])
    assert data.y.tolist() == ["a", "b", "a", "b"]


@pytest.mark.parametrize("text", [
    "", "x,target\n1,a\n2,a\n3,a\n", "x,target\n1,a\n2,b\ninf,a\n",
    "x,target\nword,a\n2,b\n3,a\n", "x,target\n1,a\n2,\n3,b\n",
    "x,target\n,a\n,b\n,a\n", "x,x,target\n1,2,a\n2,3,b\n3,4,a\n",
    "x,other\n1,a\n2,b\n3,a\n", "x,target\n1,a,extra\n2,b\n3,a\n",
])
def test_invalid_csv(tmp_path, text):
    path = tmp_path / "data.csv"
    path.write_text(text)
    with pytest.raises(DatasetError):
        load_dataset({"type": "csv", "target": "target"}, path)


def test_splits_disjoint_exhaustive_reproducible(documents):
    data = load_dataset(documents["dataset"]["source"])
    split = split_dataset(data, documents["dataset"]["split"], 42)
    partitions = [set(part) for part in split.as_dict().values()]
    assert [len(part) for part in partitions] == [90, 30, 30]
    assert partitions[0].isdisjoint(partitions[1])
    assert partitions[0].isdisjoint(partitions[2])
    assert partitions[1].isdisjoint(partitions[2])
    assert set.union(*partitions) == set(range(150))
    assert split.as_dict() == split_dataset(data, documents["dataset"]["split"], 42).as_dict()
    assert split.as_dict() != split_dataset(data, documents["dataset"]["split"], 7).as_dict()
    for indices in split.as_dict().values():
        assert set(data.y[indices]) == {0, 1, 2}


def test_insufficient_class_counts(documents):
    data = Dataset(np.arange(6).reshape(3, 2), np.array([0, 0, 1]), ("a", "b"), "")
    with pytest.raises(DatasetError):
        split_dataset(data, documents["dataset"]["split"], 42)


def test_real_training_metrics_and_reproducibility(documents, tmp_path):
    import joblib
    trainer = SklearnTrainer()
    result = trainer.train(documents["training"], documents["dataset"])
    repeat = trainer.train(documents["training"], documents["dataset"])
    assert len(result.metrics) == 8
    assert all(0 <= value <= 1 for value in result.metrics.values())
    assert result.metrics["test_accuracy"] > 0.85
    assert result.duration_seconds > 0
    assert repeat.metrics == result.metrics
    np.testing.assert_array_equal(result.model["classifier"].coef_, repeat.model["classifier"].coef_)
    loaded = joblib.load(trainer.save(result, tmp_path))
    np.testing.assert_array_equal(loaded.predict(result.dataset.X), result.model.predict(result.dataset.X))
    expected = np.mean(result.model.predict(result.dataset.X[result.split.test]) == result.dataset.y[result.split.test])
    assert result.metrics["test_accuracy"] == expected


def test_no_data_leakage_in_imputer_scaler_or_classifier(documents, monkeypatch):
    from signalflow.ml.adapters import sklearn as adapter
    original = load_dataset(documents["dataset"]["source"])
    X = original.X.copy()
    X[::7, 0] = np.nan
    raw = Dataset(X, original.y, original.features, original.sha256)
    monkeypatch.setattr(adapter, "load_dataset", lambda *args: raw)
    result = SklearnTrainer().train(documents["training"], documents["dataset"])
    train = X[result.split.train]
    np.testing.assert_allclose(result.model["imputer"].statistics_, np.nanmedian(train, axis=0))
    imputed_train = result.model["imputer"].transform(train)
    np.testing.assert_allclose(result.model["scaler"].mean_, imputed_train.mean(axis=0))
    # Modifier radicalement validation/test ne doit changer aucun paramètre appris.
    changed_X = X.copy()
    changed_X[np.concatenate([result.split.validation, result.split.test])] = 100000
    changed = Dataset(changed_X, raw.y, raw.features, raw.sha256)
    monkeypatch.setattr(adapter, "load_dataset", lambda *args: changed)
    other = SklearnTrainer().train(documents["training"], documents["dataset"])
    np.testing.assert_array_equal(result.model["imputer"].statistics_, other.model["imputer"].statistics_)
    np.testing.assert_array_equal(result.model["scaler"].mean_, other.model["scaler"].mean_)
    np.testing.assert_array_equal(result.model["classifier"].coef_, other.model["classifier"].coef_)


def test_non_convergence_is_failure(documents):
    documents["training"]["model"]["parameters"]["max_iter"] = 1
    with pytest.raises(TrainingError):
        SklearnTrainer().train(documents["training"], documents["dataset"])
