"""Client Ollama : requêtes bornées et erreurs sans fuite d'URL."""

import io
import json

import pytest

from signalflow.llm.adapters.ollama import OllamaClient
from signalflow.llm.errors import OllamaConfigError, OllamaRequestError

SETTINGS = {
    "base_url": "http://ollama.example:11434/",
    "model": "llama3.2:3b",
    "options": {"temperature": 0.2},
    "timeout_seconds": 5,
    "max_response_bytes": 4096,
}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()


class FakeOpen:
    def __init__(self, payload, status=None):
        self.body = json.dumps(payload).encode()
        self.calls = []
        self.status = status

    def __call__(self, request, timeout):
        self.calls.append((request, timeout))
        return Response(self.body)


@pytest.fixture
def client(monkeypatch):
    opened = FakeOpen({
        "model": "llama3.2:3b", "message": {"role": "assistant", "content": "réponse"},
        "done": True, "prompt_eval_count": 7, "eval_count": 3, "eval_duration": 1000000000,
    })
    monkeypatch.setattr("signalflow.llm.adapters.ollama.urlopen", opened)
    return OllamaClient(SETTINGS), opened


def test_chat_payload_and_normalization(client):
    adapter, opened = client
    result = adapter.chat([{"role": "user", "content": "bonjour"}], options={"top_p": 0.9})
    request, timeout = opened.calls[0]
    payload = json.loads(request.data)
    assert request.full_url == "http://ollama.example:11434/api/chat"
    assert timeout == 5
    assert payload["stream"] is False
    assert payload["options"] == {"temperature": 0.2, "top_p": 0.9}
    assert result["text"] == "réponse"
    assert result["prompt_eval_count"] == 7
    assert result["tokens_per_second"] == 3.0
    assert result["latency_seconds"] >= 0


def test_generate_and_tags(client):
    import json

    adapter, opened = client
    opened.body = json.dumps({"model": "llama3.2:3b", "response": "réponse", "done": True}).encode()
    assert adapter.generate("question")["text"] == "réponse"
    assert opened.calls[-1][0].full_url.endswith("/api/generate")
    opened.body = json.dumps({"models": []}).encode()
    assert adapter.tags() == []
    assert opened.calls[-1][0].full_url.endswith("/api/tags")


@pytest.mark.parametrize("messages", [[], "texte", [{"role": "root", "content": "x"}],
                                      [{"role": "user", "content": ""}]])
def test_invalid_messages(client, messages):
    adapter, _ = client
    with pytest.raises(OllamaRequestError):
        adapter.chat(messages)


def test_response_too_large(monkeypatch):
    payload = {"response": "x" * 100}
    opened = FakeOpen(payload)
    monkeypatch.setattr("signalflow.llm.adapters.ollama.urlopen", opened)
    adapter = OllamaClient({**SETTINGS, "max_response_bytes": 32})
    with pytest.raises(OllamaRequestError):
        adapter.generate("question")


def test_missing_api_key(monkeypatch):
    monkeypatch.delenv("OLLAMA_TOKEN", raising=False)
    adapter = OllamaClient({**SETTINGS, "api_key_env": "OLLAMA_TOKEN"})
    with pytest.raises(OllamaConfigError):
        adapter.generate("question")


def test_api_key_is_read_from_environment(monkeypatch):
    monkeypatch.setenv("OLLAMA_TOKEN", "secret-token")
    opened = FakeOpen({"response": "ok"})
    monkeypatch.setattr("signalflow.llm.adapters.ollama.urlopen", opened)
    adapter = OllamaClient({**SETTINGS, "api_key_env": "OLLAMA_TOKEN"})
    adapter.generate("question")
    assert opened.calls[0][0].headers["Authorization"] == "Bearer secret-token"
