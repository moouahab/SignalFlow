"""Vérifier les unités et limites avec horloges/sondes déterministes."""

import threading

import pytest

from signalflow.monitoring import NullInstrumentation
from signalflow.monitoring.local import LocalMonitor, MonitoringError
from signalflow.monitoring.resources import Reading


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class Probe:
    def __init__(self, readings):
        self.readings = iter(readings)

    def environment(self):
        return {"resource_scope": "test"}

    def read(self):
        result = next(self.readings)
        if isinstance(result, Exception):
            raise result
        return result


def test_stage_units_cpu_and_rss():
    clock = Clock()
    monitor = LocalMonitor(Probe([Reading(100, 1), Reading(300, 3)]), clock=clock)
    monitor.start({"interval_seconds": 10, "max_samples": 2})
    with monitor.stage("model_fit"):
        clock.value = 4
    result = monitor.stop(pipeline_started_at=-1)
    metrics = result["metrics"]
    assert result["complete"]
    assert metrics["stage_model_fit_seconds"] == 4
    assert metrics["pipeline_total_seconds"] == 5
    assert metrics["process_cpu_seconds"] == 2
    assert metrics["process_cpu_mean_percent"] == 50
    assert metrics["process_rss_sampled_max_bytes"] == 300
    assert metrics["process_rss_sample_mean_bytes"] == 200
    assert metrics["sampling_count"] == 2
    assert monitor.stop() is result
    assert not monitor._thread.is_alive()


def test_cpu_can_exceed_one_core_and_samples_are_bounded():
    clock = Clock()
    monitor = LocalMonitor(Probe([Reading(10, 0), Reading(20, 4), Reading(500, 8)]), clock=clock)
    monitor.start({"interval_seconds": 10, "max_samples": 2})
    clock.value = 2
    monitor._sample()
    clock.value = 4
    result = monitor.stop()
    assert result["metrics"]["process_cpu_mean_percent"] == 200
    assert result["metrics"]["process_rss_sampled_max_bytes"] == 500
    assert len(result["samples"]) == 2
    assert result["sampling"]["dropped"] == 1
    assert result["metrics"]["sampling_max_gap_seconds"] == 2


@pytest.mark.parametrize("error", [ValueError("bad"), KeyboardInterrupt()])
def test_error_and_interrupt_preserve_stage_duration(error):
    clock = Clock()
    monitor = LocalMonitor(Probe([Reading(1, 0), Reading(1, 0)]), clock=clock)
    monitor.start({"interval_seconds": 10, "max_samples": 2})
    try:
        with pytest.raises(type(error)):
            with monitor.stage("model_fit"):
                clock.value = 3
                raise error
    finally:
        result = monitor.stop()
    assert result["failed_stages"] == ["model_fit"]
    assert result["metrics"]["stage_model_fit_seconds"] == 3
    assert not monitor._thread.is_alive()


def test_initial_read_error_is_typed_and_sanitized():
    monitor = LocalMonitor(Probe([RuntimeError("SECRET")]))
    with pytest.raises(MonitoringError) as caught:
        monitor.start({"interval_seconds": 0.05, "max_samples": 2})
    assert "SECRET" not in str(caught.value)
    assert monitor._thread is None


def test_final_sample_failure_marks_incomplete():
    monitor = LocalMonitor(Probe([Reading(1, 0), OSError("SECRET")]))
    monitor.start({"interval_seconds": 10, "max_samples": 2})
    result = monitor.stop()
    assert not result["complete"]
    assert result["error"] == "resource_sampling_failed"
    assert result["metrics"]["sampling_count"] == 1


def test_background_failure_stops_without_leaking_thread():
    signaled = threading.Event()

    class FailingProbe:
        count = 0

        def environment(self):
            return {}

        def read(self):
            self.count += 1
            if self.count > 1:
                signaled.set()
                raise OSError("SECRET")
            return Reading(1, 0)

    monitor = LocalMonitor(FailingProbe())
    monitor.start({"interval_seconds": 0.02, "max_samples": 10})
    try:
        assert signaled.wait(5), "collector thread never reached probe"
    finally:
        result = monitor.stop()
    assert not result["complete"]
    assert not monitor._thread.is_alive()


def test_disabled_observer_propagates_error_without_threads():
    before = {thread.ident for thread in threading.enumerate()}
    with pytest.raises(ValueError):
        with NullInstrumentation().stage("stage"):
            raise ValueError("expected")
    assert before == {thread.ident for thread in threading.enumerate()}


def test_lifecycle_rejects_invalid_order():
    monitor = LocalMonitor(Probe([Reading(1, 0), Reading(1, 0)]))
    with pytest.raises(MonitoringError):
        monitor.stop()
    monitor.start({"interval_seconds": 10, "max_samples": 2})
    try:
        with pytest.raises(MonitoringError):
            monitor.start({"interval_seconds": 10, "max_samples": 2})
    finally:
        monitor.stop()


def test_thread_start_failure_is_typed(monkeypatch):
    monitor = LocalMonitor(Probe([Reading(1, 0)]))

    def fail_start(thread):
        raise RuntimeError("SECRET")

    monkeypatch.setattr(threading.Thread, "start", fail_start)
    with pytest.raises(MonitoringError, match="thread") as caught:
        monitor.start({"interval_seconds": 0.05, "max_samples": 2})
    assert "SECRET" not in str(caught.value)
    assert monitor._thread is None
    assert not monitor._started
