"""Contrôles API réels sur une stack préparée et démarrée ; opt-in --compose."""
import json
from pathlib import Path
import subprocess
import time
from urllib.parse import urlencode
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
GRAFANA = 'http://127.0.0.1:3000'
PROMETHEUS = 'http://127.0.0.1:9090'


def get(base, path, params=None):
    url = base + path + ('?' + urlencode(params) if params else '')
    with urlopen(url, timeout=10) as response:
        return json.load(response)


def query(expression):
    result = get(PROMETHEUS, '/api/v1/query', {'query': expression})
    assert result['status'] == 'success'
    return result['data']['result']


def test_all_targets_are_healthy():
    # A restart precedes this check: wait for each target's first fresh scrape.
    deadline = time.monotonic() + 60
    while True:
        targets = get(PROMETHEUS, '/api/v1/targets')['data']['activeTargets']
        assert {t['labels']['job'] for t in targets} == {'signalflow','node','cadvisor','prometheus','alloy','loki'}
        if all(t['health'] == 'up' for t in targets):
            break
        assert time.monotonic() < deadline, targets
        time.sleep(2)


def test_real_host_and_container_resources():
    for metric in ('node_cpu_seconds_total', 'node_memory_MemAvailable_bytes',
                   'node_filesystem_avail_bytes', 'node_disk_read_bytes_total',
                   'node_network_receive_bytes_total', 'container_cpu_usage_seconds_total{name!=""}',
                   'container_memory_working_set_bytes{name!=""}',
                   'container_network_receive_bytes_total{name!=""}'):
        assert query(metric), metric


def test_openmetrics_http_negotiation():
    code = '''import urllib.request
r=urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:9108/metrics',headers={'Accept':'application/openmetrics-text; version=1.0.0'}))
assert 'application/openmetrics-text' in r.headers['Content-Type']
assert r.read().endswith(b'# EOF\\n')
'''
    subprocess.run(['docker','compose','exec','-T','signalflow-exporter','python','-c',code], cwd=ROOT, check=True)


def test_completed_training_metrics_and_mlflow():
    assert float(query('signalflow_trainings_succeeded_total')[0]['value'][1]) >= 1
    assert float(query('signalflow_process_rss_sampled_max_bytes')[0]['value'][1]) > 0
    assert float(query('signalflow_collection_complete')[0]['value'][1]) == 1
    report = json.loads((ROOT/'outputs/observability-validation/demo-report.json').read_text())
    from mlflow.tracking import MlflowClient
    run = MlflowClient(tracking_uri=report['tracking_uri']).get_run(report['run_id'])
    assert run.info.status == 'FINISHED'
    assert run.data.metrics['validation_accuracy'] == report['metrics']['validation_accuracy']


def test_loki_has_events_for_real_run():
    report = json.loads((ROOT/'outputs/observability-validation/demo-report.json').read_text())
    params = {'query': '{service_name="signalflow"} | json | run_id="' + report['run_id'] + '"',
              'start': str(time.time_ns()-86400*10**9), 'end': str(time.time_ns()), 'limit': 1000}
    result = get(GRAFANA, '/api/datasources/proxy/uid/loki/loki/api/v1/query_range', params)
    values = [json.loads(v[1]) for stream in result['data']['result'] for v in stream['values']]
    assert any(v['event']=='train_succeeded' for v in values)
    assert {'model_fit','predict_evaluate','artifact_save'} <= {v['stage'] for v in values}
    for stream in result['data']['result']:
        # JSON parsing in the query adds fields to result labels; inspect raw streams separately.
        assert stream['stream']['service_name'] == 'signalflow'
    # query_range also returns structured metadata; /series describes indexed labels.
    indexed = get(GRAFANA, '/api/datasources/proxy/uid/loki/loki/api/v1/series',
                  {'match[]': '{service_name="signalflow"}', 'start': params['start'], 'end': params['end']})
    assert all(not ({'run_id','execution_id','filename'} & set(labels)) for labels in indexed['data'])


def test_dashboards_queries_return_real_data():
    datasources = get(GRAFANA, '/api/datasources')
    assert {'prometheus','loki'} <= {d['uid'] for d in datasources}
    for uid in ('signalflow-overview','signalflow-resources','signalflow-logs'):
        dashboard = get(GRAFANA, '/api/dashboards/uid/'+uid)['dashboard']
        assert len(dashboard['panels']) >= 6
        for panel in dashboard['panels']:
            expr = panel['targets'][0]['expr'].replace('$__rate_interval','1m').replace('$execution_id','.*')
            if panel['datasource']['uid'] == 'prometheus':
                values = query(expr)
                if not expr.startswith('ALERTS') and 'benchmarks_succeeded' not in expr:
                    assert values, (uid,panel['title'])
            else:
                result = get(GRAFANA, '/api/datasources/proxy/uid/loki/loki/api/v1/query_range',
                             {'query':expr,'start':str(time.time_ns()-86400*10**9),'end':str(time.time_ns()),'limit':100})
                assert result['status']=='success'
                if panel['title'] != 'Erreurs':
                    assert result['data']['result'], panel['title']


def test_readiness_and_prometheus_alert_rules():
    code = '''import time, urllib.request
for url in ('http://loki:3100/ready', 'http://alloy:12345/-/ready'):
    deadline = time.monotonic() + 60
    while True:
        try:
            assert urllib.request.urlopen(url, timeout=5).status == 200
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise
            time.sleep(2)
'''
    subprocess.run(['docker','compose','exec','-T','signalflow-exporter','python','-c',code],
                   cwd=ROOT, check=True, timeout=130)
    groups = get(PROMETHEUS, '/api/v1/rules')['data']['groups']
    rules = [rule for group in groups for rule in group['rules']]
    assert {'ObservabilityTargetDown','SignalFlowSnapshotUnreadable',
            'SignalFlowCollectionIncomplete','SignalFlowTrainingFailed'} <= {rule['name'] for rule in rules}
    assert all(rule['health'] == 'ok' for rule in rules)
