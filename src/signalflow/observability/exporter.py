"""Exporteur persistant Prometheus/OpenMetrics : lecture seule d'un snapshot local."""
from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any
from wsgiref.simple_server import make_server

from prometheus_client import CollectorRegistry, make_wsgi_app
from prometheus_client.core import CounterMetricFamily, GaugeMetricFamily, HistogramMetricFamily

from .metrics import BUCKETS, PHASES, read_state


class SnapshotCollector:
    """Adapter l'état borné au client officiel, sans rééchantillonner les ressources."""

    def __init__(self, directory: Path) -> None:
        """Conserver la racine partagée en lecture seule ; aucun fichier créé."""
        self.path = directory / 'metrics.json'

    def collect(self) -> Iterator[Any]:
        """Produire des familles à cardinalité bornée ; corruption signalée par une jauge.

        L'état absent expose des compteurs nuls et snapshot_available=0. L'état
        illisible expose snapshot_read_success=0, sans fabriquer de mesures ML.
        """
        try:
            state = read_state(self.path)
            available = int(self.path.exists())
            # Construire avant de céder les métriques pour éviter une réponse partielle.
            families = list(self._families(state))
        except (OSError, ValueError, KeyError, TypeError):
            yield GaugeMetricFamily('signalflow_snapshot_read_success', 'Snapshot readable', value=0)
            return
        yield GaugeMetricFamily('signalflow_snapshot_read_success', 'Snapshot readable', value=1)
        yield GaugeMetricFamily('signalflow_snapshot_available', 'Persistent snapshot exists', value=available)
        yield from families

    def _families(self, state: dict[str, Any]) -> Iterator[Any]:
        for job, prefix in (('train', 'trainings'), ('benchmark', 'benchmarks')):
            for status, value in state['counts'][job].items():
                yield CounterMetricFamily(f'signalflow_{prefix}_{status}', f'{job} {status}', value=value)
        yield CounterMetricFamily('signalflow_process_cpu_seconds', 'Cumulative sampled process CPU, threads included',
                                  value=state['cpu_seconds'])
        for name, key, help_text in (
            ('signalflow_process_rss_sampled_max_bytes', 'rss_bytes', 'Last completed training sampled RSS maximum, not guaranteed peak'),
            ('signalflow_collection_complete', 'collection_complete', 'Last training collection complete'),
            ('signalflow_monitoring_enabled', 'monitoring_enabled', 'Last training had a local observation'),
            ('signalflow_last_publication_timestamp_seconds', 'updated_at', 'Unix time of last publication'),
        ):
            yield GaugeMetricFamily(name, help_text, value=state[key])
        phases = HistogramMetricFamily('signalflow_phase_duration_seconds', 'Observed phase wall duration', labels=['phase'])
        for name, histogram in state['histograms'].items():
            buckets = [(str(boundary), count) for boundary, count in zip(BUCKETS, histogram['buckets'])]
            buckets.append(('+Inf', histogram['count']))
            if name in PHASES:
                phases.add_metric([name], buckets, histogram['sum'])
            elif name in ('train', 'benchmark'):
                yield HistogramMetricFamily(f'signalflow_{name}_duration_seconds', 'Pipeline duration excluding final telemetry publication',
                                            buckets=buckets, sum_value=histogram['sum'])
        yield phases


def application(directory: Path) -> Callable[..., Iterable[bytes]]:
    """Construire l'application WSGI avec négociation OpenMetrics et endpoint /health.

    Aucun registre global, aucun compteur auto de processus de l'exporteur mélangé
    aux mesures du pipeline. /health confirme le serveur ; la jauge signale les I/O.
    """
    registry = CollectorRegistry()
    registry.register(SnapshotCollector(directory))
    metrics = make_wsgi_app(registry)

    def app(environ: dict[str, Any], start_response: Callable[..., Any]) -> Iterable[bytes]:
        if environ['PATH_INFO'] == '/health':
            start_response('200 OK', [('Content-Type', 'text/plain')])
            return [b'ok\n']
        if environ['PATH_INFO'] != '/metrics':
            start_response('404 Not Found', [('Content-Type', 'text/plain')])
            return [b'not found\n']
        return metrics(environ, start_response)
    return app


def main() -> None:
    """Servir le snapshot local ; arguments explicites, aucune destination réseau YAML."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=9108)
    args = parser.parse_args()
    with make_server(args.host, args.port, application(args.directory)) as server:
        server.serve_forever()


if __name__ == '__main__':
    main()
