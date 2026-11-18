"""Publication des appels Ollama dans MLflow, avec client injecté."""

import json
from types import SimpleNamespace

from signalflow.llm.tracing import OllamaTracker

SETTINGS = {"experiment_name": "ollama", "run_name": "run", "native_traces": False}
RESULT = {
    "model": "llama3.2:3b", "text": "réponse", "prompt_eval_count": 5,
    "eval_count": 2, "tokens_per_second": 4.0, "total_duration_ns": 10,
    "prompt_eval_duration_ns": 5, "eval_duration_ns": 5, "latency_seconds": 0.01,
}


class FakeOllama:
    model = "llama3.2:3b"

    def chat(self, messages, options=None):
        return dict(RESULT)

    def generate(self, prompt, options=None):
        return dict(RESULT)


class FakeMLflowClient:
    tracking_uri = "sqlite:////tmp/fake.db"

    def get_run(self, run_id):
        return SimpleNamespace(info=SimpleNamespace(experiment_id="1"))

    def log_artifact(self, *args, **kwargs):
        pass

    def start_trace(self, **kwargs):
        return SimpleNamespace(trace_id="tr-1", span_id="sp-1")

    def start_span(self, **kwargs):
        return SimpleNamespace(span_id="sp-2")

    def end_span(self, **kwargs):
        pass

    def end_trace(self, **kwargs):
        pass


class FakeTracker:
    def __init__(self):
        self.client = FakeMLflowClient()
        self.logged = []
        self.finished = None

    def start(self, settings, output):
        return "run-1"

    def log(self, run_id, parameters, metrics, artifacts):
        self.logged.append((parameters, metrics, artifacts))

    def finish(self, run_id, status="FINISHED"):
        self.finished = status


def make_tracker(tmp_path, **settings):
    adapter = FakeTracker()
    tracker = OllamaTracker(
        FakeOllama(), {**SETTINGS, **settings}, tmp_path / "run", tmp_path, tracker=adapter,
    )
    tracker.start()
    return tracker, adapter


def test_chat_writes_run_metrics_and_artifact(tmp_path):
    tracker, adapter = make_tracker(tmp_path)
    result = tracker.chat([{"role": "user", "content": "bonjour"}])
    assert result["text"] == "réponse"
    parameters, metrics, artifacts = adapter.logged[0]
    assert parameters["provider"] == "ollama"
    assert metrics["ollama.completion_tokens"] == 2
    record = json.loads((artifacts / "call.json").read_text())
    assert record["request"][0]["content"] == "bonjour"
    assert record["response"]["text"] == "réponse"
    tracker.finish()
    assert adapter.finished == "FINISHED"


def test_native_trace_creates_span(tmp_path, monkeypatch):
    calls = []
    client = FakeMLflowClient()

    def start_span(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(span_id="sp-2")

    client.start_span = start_span
    tracker, adapter = make_tracker(tmp_path, native_traces=True)
    adapter.client = client
    tracker.chat([{"role": "user", "content": "bonjour"}])
    assert calls and calls[0]["span_type"] == "LLM"
    assert tracker.trace_ids == ["tr-1"]
    tracker.finish()


def test_prompts_can_be_redacted(tmp_path):
    tracker, adapter = make_tracker(tmp_path, log_prompts=False)
    tracker.generate("secret prompt")
    parameters, metrics, artifacts = adapter.logged[0]
    record = json.loads((artifacts / "call.json").read_text())
    assert record["request"] == "<redacted>"
    assert record["response"]["text"] == "<redacted>"
    assert metrics["ollama.prompt_tokens"] == 5
