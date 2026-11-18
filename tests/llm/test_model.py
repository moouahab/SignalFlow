"""Modèle pyfunc Ollama : sauvegarde locale et publication MLflow."""

import json
from types import SimpleNamespace

import pytest

pytest.importorskip("mlflow")

from signalflow.llm.model import OllamaPyFunc, log_model, register_model, save_model

SETTINGS = {"base_url": "http://ollama.example:11434", "model": "llama3.2:3b", "options": {}}


def test_prompts_accepts_supported_shapes():
    assert OllamaPyFunc._prompts("bonjour") == ["bonjour"]
    assert OllamaPyFunc._prompts(["a", "b"]) == ["a", "b"]
    assert OllamaPyFunc._prompts([[ "a" ]]) == ["a"]
    with pytest.raises(Exception):
        OllamaPyFunc._prompts([])


def test_save_and_load_model(tmp_path, monkeypatch):
    from signalflow.llm.adapters import ollama

    monkeypatch.setattr(ollama.OllamaClient, "generate", lambda self, prompt, options=None: {"text": f"écho:{prompt}"})
    path = save_model(tmp_path / "model", SETTINGS)
    assert (path / "MLmodel").exists()
    assert json.loads((path / "ollama_settings.json").read_text()) == SETTINGS
    import mlflow.pyfunc

    loaded = mlflow.pyfunc.load_model(str(path))
    assert loaded.predict(["a", "b"]) == ["écho:a", "écho:b"]


def test_log_and_register_model(tmp_path):
    class Client:
        def __init__(self):
            self.logged = []

        def log_artifacts(self, run_id, path, artifact_path=None):
            self.logged.append((run_id, path, artifact_path))

        def get_run(self, run_id):
            return SimpleNamespace(info=SimpleNamespace(artifact_uri="file:///tmp/artifacts"))

        def create_registered_model(self, name):
            return SimpleNamespace(name=name)

        def create_model_version(self, name, source, run_id=None):
            return SimpleNamespace(version="3")

        def set_registered_model_alias(self, name, alias, version):
            self.alias = (name, alias, version)

    client = Client()
    source = log_model(client, "run-1", tmp_path)
    assert source == "file:///tmp/artifacts/model"
    assert client.logged[0][2] == "model"
    assert register_model(client, "run-1", source, "ollama_test", alias="champion") == "3"
    assert client.alias == ("ollama_test", "champion", "3")


def test_register_reuses_existing_registered_model():
    """MLflow 3.18 renvoie parfois 'RESOURCE_ALREADY_EXISTS' comme chaîne."""
    from mlflow.exceptions import MlflowException

    class Client:
        def create_registered_model(self, name):
            error = MlflowException("already exists")
            error.error_code = "RESOURCE_ALREADY_EXISTS"
            raise error

        def create_model_version(self, name, source, run_id=None):
            return SimpleNamespace(version="4")

    source = "file:///tmp/artifacts/model"
    assert register_model(Client(), "run-2", source, "ollama_test") == "4"
