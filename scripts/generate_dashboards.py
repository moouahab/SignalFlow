"""Générer les trois dashboards provisionnés à partir de requêtes versionnées."""
from pathlib import Path
import json
from typing import Any

ROOT = Path(__file__).resolve().parents[1] / 'infrastructure/observability/grafana/dashboards'


def panel(title: str, expr: str, unit: str = 'short', kind: str = 'timeseries',
          description: str = '', datasource: str = 'prometheus',
          legend: str = '{{phase}}{{name}}{{job}}') -> dict[str, Any]:
    """Construire un panneau avec une requête et ses unités explicites."""
    if datasource == 'loki':
        expr += ' | line_format "{{.level}} {{.component}}/{{.stage}} {{.event}} run={{.run_id}} exec={{.execution_id}}"'
    return {'title': title, 'type': kind, 'description': description,
            'datasource': {'type': 'loki' if datasource == 'loki' else 'prometheus', 'uid': datasource},
            'targets': [{'refId': 'A', 'expr': expr, 'legendFormat': legend,
                         'queryType': 'range',
                         **({'instant': True, 'range': False, 'format': 'table'}
                            if kind == 'table' and datasource == 'prometheus' else {})}],
            'fieldConfig': {'defaults': {'unit': unit, 'color': {'mode': 'palette-classic'},
                             'thresholds': {'mode': 'absolute', 'steps': [{'color': 'green', 'value': None}]}},
                            'overrides': []},
            'options': {'reduceOptions': {'calcs': ['lastNotNull'], 'fields': '', 'values': False},
                        'legend': {'displayMode': 'table', 'placement': 'bottom'},
                        'showTime': True, 'showLabels': False, 'showLogAttributes': False, 'prettifyLogMessage': False,
                        'enableLogDetails': True, 'wrapLogMessage': True, 'sortOrder': 'Descending'}}


def write(uid: str, title: str, panels: list[dict[str, Any]],
          variables: list[dict[str, Any]] | None = None) -> None:
    """Positionner les panneaux et écrire un dashboard JSON provisionnable."""
    for index, item in enumerate(panels):
        item['id'] = index + 1
        if item['title'] == 'Entraînements échoués':
            item['fieldConfig']['defaults']['thresholds']['steps'] = [
                {'color': 'green', 'value': None}, {'color': 'red', 'value': 1}]
            item['fieldConfig']['defaults']['color'] = {'mode': 'thresholds'}
        elif item['type'] == 'stat' and ('Collecte complète' in item['title'] or item['title'] in ('Targets Prometheus', 'État de lecture du snapshot')):
            item['fieldConfig']['defaults']['thresholds']['steps'] = [
                {'color': 'red', 'value': None}, {'color': 'green', 'value': 1}]
            item['fieldConfig']['defaults']['color'] = {'mode': 'thresholds'}
        item['gridPos'] = {'x': (index % 2) * 12, 'y': (index // 2) * 8, 'w': 12, 'h': 8}
    document = {'uid': uid, 'title': title, 'tags': ['signalflow', 'local'], 'schemaVersion': 39,
                'version': 1, 'editable': False, 'timezone': 'browser', 'refresh': '15s',
                'time': {'from': 'now-30m', 'to': 'now'}, 'panels': panels,
                'templating': {'list': variables or []},
                'links': [{'title': 'Overview', 'url': '/d/signalflow-overview'},
                          {'title': 'Infrastructure', 'url': '/d/signalflow-resources'},
                          {'title': 'Logs', 'url': '/d/signalflow-logs'}]}
    (ROOT / f'{uid}.json').write_text(json.dumps(document, indent=2) + '\n')


write('signalflow-overview', 'SignalFlow Overview', [
    panel('Entraînements démarrés', 'signalflow_trainings_started_total', kind='stat'),
    panel('Entraînements réussis', 'signalflow_trainings_succeeded_total', kind='stat'),
    panel('Entraînements échoués', 'signalflow_trainings_failed_total', kind='stat'),
    panel('Benchmarks terminés', 'signalflow_benchmarks_succeeded_total', kind='stat'),
    panel('Durée moyenne cumulée du pipeline', 'signalflow_train_duration_seconds_sum / clamp_min(signalflow_train_duration_seconds_count, 1)', 's', description='Moyenne de tous les runs observés, publication finale exclue ; pas une mesure de latence réseau.'),
    panel('Durées moyennes par phase', 'signalflow_phase_duration_seconds_sum / clamp_min(signalflow_phase_duration_seconds_count, 1)', 's', legend='{{phase}}'),
    panel('Évolution des terminaisons / seconde', 'rate(signalflow_trainings_succeeded_total[$__rate_interval])', 'ops'),
    panel('Alertes actives', 'ALERTS{alertstate="firing"}', kind='table', description='État actuel ; une table vide signifie aucune alerte active.', legend='{{alertname}}'),
    panel('Dernière publication', 'signalflow_last_publication_timestamp_seconds * 1000', 'dateTimeAsIso', 'stat'),
    panel('Collecte complète du dernier entraînement', 'signalflow_collection_complete', kind='stat', description='À interpréter avec signalflow_monitoring_enabled : 0 si monitoring désactivé.'),
])
write('signalflow-resources', 'Infrastructure & Resources', [
    panel('CPU Linux du démon Docker', '100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[$__rate_interval])))', 'percent', description='Sous WSL2 : noyau Linux/VM, pas charge CPU globale Windows.'),
    panel('RAM Linux utilisée', 'node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes', 'bytes'),
    panel('CPU des conteneurs', 'sum by (name) (rate(container_cpu_usage_seconds_total{name!=""}[$__rate_interval]))', 'cores'),
    panel('RAM des conteneurs (working set)', 'container_memory_working_set_bytes{name!=""}', 'bytes', legend='{{name}}'),
    panel('Dernier RSS maximal observé du processus ML', 'signalflow_process_rss_sampled_max_bytes', 'bytes', description='Maximum échantillonné ; pics courts possibles entre lectures. Inclut SDK et pages partagées.'),
    panel('CPU cumulé des entraînements', 'signalflow_process_cpu_seconds_total', 's', description='Processus Python et threads du collecteur ; enfants exclus.'),
    panel('Disque Linux disponible', 'node_filesystem_avail_bytes{fstype!~"tmpfs|overlay|squashfs"}', 'bytes', legend='{{mountpoint}} ({{device}})'),
    panel('Lectures disque Linux', 'rate(node_disk_read_bytes_total[$__rate_interval])', 'Bps', legend='{{device}}'),
    panel('Réseau Linux reçu', 'rate(node_network_receive_bytes_total{device!="lo"}[$__rate_interval])', 'Bps', legend='{{device}}'),
    panel('Réseau des conteneurs reçu', 'sum by (name) (rate(container_network_receive_bytes_total{name!="",interface!="lo"}[$__rate_interval]))', 'Bps'),
    panel('Targets Prometheus', 'up', kind='stat'),
    panel('État de lecture du snapshot', 'signalflow_snapshot_read_success', kind='stat'),
])
base = '{service_name="signalflow"} | json | execution_id=~"$execution_id"'
write('signalflow-logs', 'Logs & Diagnostics', [
    panel('Événements SignalFlow', base, kind='logs', datasource='loki'),
    panel('Erreurs', base + ' | level="ERROR"', kind='logs', datasource='loki'),
    panel('Démarrages et finalisations', base + ' | event=~"train_started|train_succeeded|train_failed|benchmark_succeeded|benchmark_failed"', kind='logs', datasource='loki'),
    panel('Phases du pipeline', base + ' | stage!=""', kind='logs', datasource='loki'),
    panel('Échecs cumulés (même fenêtre temporelle)', 'signalflow_trainings_failed_total', description='Corrélation temporelle agrégée ; pas de label run_id dans Prometheus.'),
    panel('Collecte complète', 'signalflow_collection_complete', kind='stat'),
], [{'name': 'execution_id', 'label': 'Execution ID (regex)', 'type': 'textbox',
     'current': {'text': '.*', 'value': '.*'}, 'query': '.*', 'hide': 0}])
