"""Lien tolérant aux pannes entre instrumentation, métriques persistantes et logs."""
from __future__ import annotations

from collections.abc import Iterator, Callable
from contextlib import contextmanager, AbstractContextManager
import os
from pathlib import Path
import sys
import time
from typing import Any

from signalflow.config.loader.errors import ConfigError
from signalflow.monitoring import Instrumentation
from .logging import JsonEvents
from .metrics import MetricsPublisher


class ObservabilityConfigError(ConfigError):
    """Destination de publication non autorisée."""
    code = 'OBSERVABILITY_CONFIG_ERROR'


def destination(settings: dict[str, Any]) -> Path:
    """Résoudre une sortie relative confinée au cwd, ou une racine imposée par l'opérateur.

    SIGNALFLOW_OBSERVABILITY_DIR est un choix de déploiement hors YAML. Aucun dossier
    créé ici. Lève ObservabilityConfigError pour traversée ou lien sortant.
    """
    override = os.environ.get('SIGNALFLOW_OBSERVABILITY_DIR')
    if override:
        return Path(override).expanduser().resolve()
    base = Path.cwd().resolve()
    raw = Path(settings.get('directory', './outputs/observability'))
    resolved = (base / raw).resolve()
    if raw.is_absolute() or '..' in raw.parts or not resolved.is_relative_to(base):
        raise ObservabilityConfigError('destination hors du dossier de travail autorisé')
    return resolved


class TelemetrySession:
    """Publication facultative ; une panne d'I/O n'interrompt jamais le calcul ML."""

    def __init__(self, settings: dict[str, Any] | None, execution_id: str, job: str = 'train') -> None:
        """Préparer les sorties actives sans I/O ; une configuration invalide est une erreur métier."""
        settings = settings or {}
        self.execution_id, self.job = execution_id, job
        self.run_id: str | None = None
        self.errors: list[str] = []
        self.io_seconds = 0.0
        self._finished = False
        self.publisher: MetricsPublisher | None = None
        self.logger: JsonEvents | None = None
        if settings.get('prometheus', {}).get('enabled', False):
            self.publisher = MetricsPublisher(destination(settings))
        log = settings.get('logging', {})
        if log.get('enabled', False):
            self.logger = JsonEvents(destination(settings), max_bytes=log.get('max_bytes', 10485760),
                                     backups=log.get('backups', 3))

    def _attempt(self, operation: Callable[[], None], code: str) -> None:
        started = time.perf_counter()
        try:
            operation()
        except Exception:
            if code not in self.errors:
                self.errors.append(code)
                print(f'SIGNALFLOW_OBSERVABILITY_WARNING: {code}', file=sys.stderr)
        finally:
            self.io_seconds += time.perf_counter() - started

    def event(self, event: str, *, stage: str = '', level: str = 'INFO') -> None:
        """Journaliser un événement interne ; une panne est signalée une fois sur stderr."""
        if self.logger:
            self._attempt(lambda: self.logger.write(event, self.execution_id, component=self.job,
                          stage=stage, level=level, run_id=self.run_id), 'structured_log_write_failed')

    def start(self) -> None:
        """Publier le démarrage après validation de configuration ; aucune connexion distante."""
        if self.publisher:
            self._attempt(lambda: self.publisher.publish(self.job, 'started'), 'metrics_publish_failed')
        self.event(f'{self.job}_started')

    def finish(self, success: bool, duration: float, observation: dict[str, Any] | None = None) -> None:
        """Finaliser une seule fois, même si l'orchestrateur repasse par son chemin d'erreur."""
        if self._finished:
            return
        self._finished = True
        status = 'succeeded' if success else 'failed'
        if self.publisher:
            self._attempt(lambda: self.publisher.publish(self.job, status, duration, observation),
                          'metrics_publish_failed')
        if observation is not None and not observation['complete']:
            self.event('collection_incomplete', level='ERROR')
        self.event(f'{self.job}_{status}', level='INFO' if success else 'ERROR')

    @contextmanager
    def stage(self, observer: Instrumentation, name: str) -> Iterator[None]:
        """Ajouter début/fin de phase à l'observateur existant, sans seconde sonde psutil."""
        self.event('stage_started', stage=name)
        try:
            with observer.stage(name):
                yield
        except BaseException:
            self.event('stage_failed', stage=name, level='ERROR')
            raise
        else:
            self.event('stage_finished', stage=name)


class LoggedInstrumentation:
    """Adaptateur du protocole stage : délègue les mesures et ajoute les événements."""

    def __init__(self, observer: Instrumentation, telemetry: TelemetrySession) -> None:
        """Conserver les deux collaborateurs ; aucun effet externe."""
        self.observer, self.telemetry = observer, telemetry

    def stage(self, name: str) -> AbstractContextManager[None]:
        """Retourner le contexte instrumenté ; propage les erreurs du bloc exécuté."""
        return self.telemetry.stage(self.observer, name)
