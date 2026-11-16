"""Durées monotones et échantillonnage local borné, sans réseau."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import math
import threading
import time
from typing import Any

from signalflow.config.loader.errors import ConfigError
from .resources import PsutilProbe, ResourceProbe


class MonitoringError(ConfigError):
    """Impossible de démarrer la mesure locale."""

    code = "MONITORING_ERROR"


class LocalMonitor:
    """Collecteur indépendant du modèle ; une instance par entraînement."""

    def __init__(self, probe: ResourceProbe | None = None, *, clock: Callable[[], float] = time.perf_counter) -> None:
        """Créer le collecteur, sans démarrer de thread.

        Args: probe: sonde injectable ou psutil local ; clock: horloge monotone.
        Retour: aucun. Exceptions: ImportError si psutil absent. Effets: lecture PID.
        """
        self._probe = probe if probe is not None else PsutilProbe()
        self._clock = clock
        self._event = threading.Event()
        self._thread: threading.Thread | None = None
        self._started = False
        self._stopped = False
        self.durations: dict[str, float] = {}
        self.failed_stages: list[str] = []
        self._samples: list[dict[str, float | int]] = []
        self._count = 0
        self._rss_sum = 0
        self._rss_peak = 0
        self._cpu_peak = 0.0
        self._max_gap = 0.0
        self._read_wall = 0.0
        self._read_cpu = 0.0
        self._error: str | None = None

    def start(self, settings: dict[str, Any]) -> None:
        """Démarrer l'échantillonnage avec interval_seconds et max_samples validés.

        Retourne None. Lève MonitoringError si démarrage impossible ou répété.
        Lit les ressources et démarre un thread ; aucune connexion réseau.
        """
        if self._started:
            raise MonitoringError("un collecteur ne peut être démarré qu'une fois")
        self._interval = settings["interval_seconds"]
        self._max_samples = settings["max_samples"]
        if not math.isfinite(self._interval) or self._interval < 0.02 or self._max_samples < 2:
            raise MonitoringError("période ou capacité d'échantillonnage invalide")
        try:
            self.hardware = self._probe.environment()
            self._origin = self._clock()
            self._sample()
        except Exception:
            raise MonitoringError("lecture initiale des ressources impossible") from None
        self._thread = threading.Thread(target=self._loop, name="signalflow-monitor", daemon=True)
        try:
            self._thread.start()
        except RuntimeError:
            self._thread = None
            raise MonitoringError("démarrage du thread de collecte impossible") from None
        self._started = True

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        """Chronométrer name avec perf_counter, y compris si le bloc échoue.

        Retourne un contexte ; propage toutes les erreurs/interruption du bloc.
        Effets : cumule la durée et marque les étapes échouées ; pas de lecture OS.
        """
        start = self._clock()
        try:
            yield
        except BaseException:
            self.failed_stages.append(name)
            raise
        finally:
            self.durations[name] = self.durations.get(name, 0.0) + self._clock() - start

    def _sample(self) -> None:
        start, cpu_start = self._clock(), time.thread_time()
        reading = self._probe.read()
        now = self._clock()
        point = {"elapsed_seconds": now - self._origin, "rss_bytes": reading.rss_bytes,
                 "cpu_seconds": reading.cpu_seconds}
        if self._count:
            gap = now - self._previous_time
            self._max_gap = max(self._max_gap, gap)
            if gap > 0:
                percent = 100 * max(0.0, reading.cpu_seconds - self._last_cpu) / gap
                self._cpu_peak = max(self._cpu_peak, percent)
        else:
            self._first_cpu, self._first_rss = reading.cpu_seconds, reading.rss_bytes
            self._first_time = now
        self._last_cpu, self._last_rss, self._previous_time = reading.cpu_seconds, reading.rss_bytes, now
        self._count += 1
        self._rss_sum += reading.rss_bytes
        self._rss_peak = max(self._rss_peak, reading.rss_bytes)
        if len(self._samples) < self._max_samples:
            self._samples.append(point)
        self._read_wall += self._clock() - start
        self._read_cpu += time.thread_time() - cpu_start

    def _loop(self) -> None:
        while not self._event.wait(self._interval):
            try:
                self._sample()
            except Exception:
                # Aucun message OS susceptible d'exposer le contexte utilisateur.
                self._error = "resource_sampling_failed"
                self._event.set()

    def stop(self, *, pipeline_started_at: float | None = None) -> dict[str, Any]:
        """Arrêter le thread puis retourner les mesures, même partielles.

        Args: pipeline_started_at: origine perf_counter du pipeline, avant validation.
        Retour: document JSON de durées, ressources, méthode et échantillons bornés.
        Lève MonitoringError si non démarré. Appels répétés retournent le même résultat.
        Effets: rejoint le thread et prend une dernière mesure. Une erreur de sonde
        donne complete=false ; le benchmark doit refuser ces données incomplètes.
        """
        if not self._started:
            raise MonitoringError("collecteur non démarré")
        if self._stopped:
            return self._result
        self._event.set()
        if self._thread:
            self._thread.join()
        try:
            self._sample()
        except Exception:
            self._error = "resource_sampling_failed"
        self._stopped = True
        window = self._previous_time - self._first_time
        cpu = max(0.0, self._last_cpu - self._first_cpu)
        metrics = {
            "collection_complete": int(self._error is None),
            **{f"stage_{name}_seconds": duration for name, duration in self.durations.items()},
            "pipeline_total_seconds": self._clock() - (pipeline_started_at if pipeline_started_at is not None else self._origin),
            "resource_window_seconds": window,
            "process_rss_start_bytes": self._first_rss, "process_rss_end_bytes": self._last_rss,
            "process_rss_sampled_max_bytes": self._rss_peak,
            "process_rss_sample_mean_bytes": self._rss_sum / self._count,
            "process_cpu_seconds": cpu,
            "process_cpu_mean_percent": 100 * cpu / window if window > 0 else 0.0,
            "process_cpu_sampled_max_percent": self._cpu_peak,
            "sampling_max_gap_seconds": self._max_gap,
            "sampling_read_wall_seconds": self._read_wall,
            "sampling_read_cpu_seconds": self._read_cpu,
            "sampling_count": self._count,
        }
        self._result = {
            "schema_version": "0.0.1", "complete": self._error is None,
            "error": self._error, "metrics": metrics, "hardware": self.hardware,
            "failed_stages": self.failed_stages, "samples": self._samples,
            "sampling": {"interval_seconds": self._interval, "max_samples": self._max_samples,
                         "retained": len(self._samples), "dropped": self._count - len(self._samples)},
            "method": {
                "clock": "perf_counter", "cpu": "delta(user+system)/actual_wall_interval*100; one core=100%; includes monitor thread; excludes children",
                "memory": "RSS snapshots; sampled maximum is a lower bound, not a guaranteed peak; shared pages included",
                "rss_mean": "arithmetic sample mean, not a time-weighted mean",
                "total_scope": "run_training entry through normal artifact tracking and run termination; excludes final telemetry publication and interpreter startup",
                "resources_scope": "after configuration validation through normal run termination",
                "overhead": "read_wall/read_cpu measure sampling callbacks only, not total observer perturbation",
            },
        }
        return self._result
