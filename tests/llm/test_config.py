"""Validation de la configuration Ollama, sans réseau."""

import pytest

from signalflow.config.loader.errors import ConfigValidationError
from signalflow.llm.config import load_llm_configuration
from signalflow.llm.errors import OllamaConfigError
from signalflow.ml.registry import create_registry


def load(directory):
    return load_llm_configuration(directory, registry=create_registry())


def test_enabled_configuration(llm_config):
    configuration = load(llm_config)
    assert configuration.ollama["model"] == "llama3.2:3b"
    assert configuration.ollama["base_url"] == "http://127.0.0.1:11434"
    assert configuration.tracking["experiment_name"] == "ollama-chat"
    assert configuration.sources["llm"].endswith("llm.yaml")


def test_disabled_provider(llm_config):
    path = llm_config / "tools.yaml"
    import yaml

    tools = yaml.safe_load(path.read_text(encoding="utf-8"))
    tools["tools"]["llm"]["enabled"] = False
    path.write_text(yaml.safe_dump(tools), encoding="utf-8")
    with pytest.raises(OllamaConfigError):
        load(llm_config)


@pytest.mark.parametrize("path,value", [
    (["ollama", "base_url"], "file:///etc/passwd"),
    (["ollama", "base_url"], "http://user:pass@example.com"),
    (["ollama", "model"], "../etc/passwd"),
    (["ollama", "timeout_seconds"], 0),
    (["ollama", "max_response_bytes"], 999999999),
    (["ollama", "options", "temperature"], 5),
    (["ollama", "extra"], 1),
    (["tracking", "experiment_name"], ""),
    (["tracking", "native_traces"], "yes"),
    (["tracking", "extra"], 1),
])
def test_invalid_settings(llm_config_patched, path, value):
    directory, read, write = llm_config_patched
    document = read()
    node = document
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    write(document)
    with pytest.raises((ConfigValidationError, OllamaConfigError)):
        load(directory)


def test_tracking_uri_rejects_credentials(llm_config_patched):
    directory, read, write = llm_config_patched
    document = read()
    document["tracking"]["tracking_uri"] = "http://user:secret@mlflow.example:5000"
    write(document)
    with pytest.raises(OllamaConfigError):
        load(directory)


def test_tracking_uri_allows_remote_server(llm_config_patched):
    directory, read, write = llm_config_patched
    document = read()
    document["tracking"]["tracking_uri"] = "http://127.0.0.1:5000"
    write(document)
    assert load(directory).tracking["tracking_uri"] == "http://127.0.0.1:5000"
