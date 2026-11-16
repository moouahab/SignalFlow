"""Agrégats de taille constante, sans identifiant de run ni collecte supplémentaire."""
from __future__ import annotations

import json
import math
from pathlib import Path
import time
from typing import Any

from .storage import atomic_json, locked

BUCKETS = (0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0, 300.0, 1800.0)
PHASES = ('initialization', 'dataset_load', 'data_split', 'preprocessing', 'model_fit',
          'predict_evaluate', 'artifact_save', 'tracking_export')
STATUSES = ('started', 'succeeded', 'failed')


def empty_state() -> dict[str, Any]:
    """Retourner un nouvel état borné des compteurs, histogrammes et dernières mesures."""
    return {'version': 1, 'counts': {job: dict.fromkeys(STATUSES, 0) for job in ('train', 'benchmark')},
            'histograms': {}, 'cpu_seconds': 0.0, 'rss_bytes': 0, 'collection_complete': 0,
            'monitoring_enabled': 0, 'updated_at': 0.0}


def read_state(path: Path) -> dict[str, Any]:
    """Lire au plus 1 Mio ; fichier absent = état initial, corruption = erreur visible."""
    try:
        with path.open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
    except FileNotFoundError:
        return empty_state()
    if len(raw) > 1024 * 1024:
        raise ValueError('oversized metrics state')
    state = json.loads(raw)
    if not isinstance(state, dict) or state.get('version') != 1:
        raise ValueError('unsupported metrics state')
    _validate_state(state)
    return state


def _validate_state(state: dict[str, Any]) -> None:
    """Refuser une corruption sans fabriquer de compteurs ni de labels nouveaux."""
    def number(value: Any) -> bool:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0

    if not isinstance(state['counts'], dict) or set(state['counts']) != {'train', 'benchmark'}:
        raise ValueError('invalid job labels')
    for counts in state['counts'].values():
        if (not isinstance(counts, dict) or set(counts) != set(STATUSES)
                or not all(number(v) and int(v) == v for v in counts.values())):
            raise ValueError('invalid counters')
    if (not isinstance(state['histograms'], dict)
            or not set(state['histograms']).issubset({'train', 'benchmark', *PHASES})):
        raise ValueError('invalid phase labels')
    for histogram in state['histograms'].values():
        if not isinstance(histogram, dict) or not isinstance(histogram.get('buckets'), list):
            raise ValueError('invalid histogram structure')
        buckets = histogram['buckets']
        if (not number(histogram['sum']) or not number(histogram['count'])
                or int(histogram['count']) != histogram['count']
                or len(buckets) != len(BUCKETS)
                or not all(number(v) and int(v) == v and v <= histogram['count'] for v in buckets)
                or buckets != sorted(buckets)):
            raise ValueError('invalid histogram')
    for key in ('cpu_seconds', 'rss_bytes', 'updated_at', 'collection_complete', 'monitoring_enabled'):
        if not number(state[key]):
            raise ValueError('invalid metric value')
    if state['collection_complete'] not in (0, 1) or state['monitoring_enabled'] not in (0, 1):
        raise ValueError('invalid collection status')


def _observe(state: dict[str, Any], name: str, value: float) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError('invalid measurement')
    histogram = state['histograms'].setdefault(name, {'sum': 0.0, 'count': 0, 'buckets': [0] * len(BUCKETS)})
    histogram['sum'] += value
    histogram['count'] += 1
    for index, boundary in enumerate(BUCKETS):
        histogram['buckets'][index] += int(value <= boundary)


class MetricsPublisher:
    """Publier des agrégats durables, sérialisés entre processus par verrou local."""

    def __init__(self, directory: Path) -> None:
        """Conserver la destination sans ouvrir de fichier ni de connexion."""
        self.directory = directory

    def publish(self, job: str, status: str, duration: float | None = None,
                observation: dict[str, Any] | None = None) -> None:
        """Ajouter un événement et les mesures existantes, puis remplacer le snapshot.

        job et status sont des ensembles fermés. Lève ValueError/OSError/TimeoutError
        sans remettre à zéro un état corrompu ; l'appelant doit signaler l'échec.
        Aucun thread, dataset ou réseau ; le snapshot survit à la fin du processus.
        """
        if job not in ('train', 'benchmark') or status not in STATUSES:
            raise ValueError('unknown metric label')
        with locked(self.directory, '.metrics.lock'):
            path = self.directory / 'metrics.json'
            state = read_state(path)
            state['counts'][job][status] += 1
            if duration is not None:
                _observe(state, job, duration)
            if job == 'train' and status != 'started':
                state['monitoring_enabled'] = int(observation is not None)
                state['collection_complete'] = int(bool(observation and observation['complete']))
                state['rss_bytes'] = 0
                if observation is not None:
                    metrics = observation['metrics']
                    cpu = float(metrics['process_cpu_seconds'])
                    rss = float(metrics['process_rss_sampled_max_bytes'])
                    if not all(math.isfinite(x) and x >= 0 for x in (cpu, rss)):
                        raise ValueError('invalid resource measurement')
                    state['cpu_seconds'] += cpu
                    state['rss_bytes'] = rss
                    for phase in PHASES:
                        key = f'stage_{phase}_seconds'
                        if key in metrics:
                            _observe(state, phase, metrics[key])
            state['updated_at'] = time.time()
            atomic_json(path, state)
