"""Tests destructifs uniquement pour la stack SignalFlow dédiée : stop puis redémarrage.

Exécuter explicitement avec --compose ; aucun volume n'est supprimé. Les services
sont toujours redémarrés dans finally, même si une assertion échoue.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlencode
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[2]


def compose(*args):
    subprocess.run(['docker','compose',*args], cwd=ROOT, check=True, capture_output=True, timeout=90)


def train(output):
    result = subprocess.run([sys.executable,'-m','signalflow','train','--config-dir',
                             str(ROOT/'examples/classification'),'--output-dir',str(output)],
                            cwd=ROOT, env=dict(os.environ, PYTHONPATH=str(ROOT/'src')),
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['status'] == 'FINISHED'
    assert report['observability']['warnings'] == []
    return report


def loki_has(run_id):
    query = '{service_name="signalflow"} | json | run_id="'+run_id+'" | event="train_succeeded"'
    params = urlencode({'query':query,'start':str(time.time_ns()-3600*10**9),'end':str(time.time_ns())})
    with urlopen('http://127.0.0.1:3000/api/datasources/proxy/uid/loki/loki/api/v1/query_range?'+params, timeout=5) as response:
        return bool(json.load(response)['data']['result'])


def eventually(predicate):
    deadline = time.monotonic()+180
    last_error = None
    while time.monotonic()<deadline:
        try:
            if predicate():
                return
        except Exception as error:
            last_error = type(error).__name__
        time.sleep(2)
    raise AssertionError(f'recovery deadline exceeded: {last_error}')


def test_loki_outage_queue_and_full_stack_restart(tmp_path):
    state_path=ROOT/'outputs/observability/metrics.json'
    before=json.loads(state_path.read_text())['counts']['train']['succeeded']
    try:
        compose('stop','loki')
        offline=train(tmp_path/'loki-offline')
        # Laisser Alloy lire les événements et les inscrire dans sa file persistante.
        time.sleep(8)
        compose('restart','alloy')
    finally:
        compose('up','-d')
    eventually(lambda:loki_has(offline['run_id']))
    try:
        compose('stop')
        stopped=train(tmp_path/'stack-stopped')
        assert json.loads(state_path.read_text())['counts']['train']['succeeded']==before+2
    finally:
        compose('up','-d')
    eventually(lambda:loki_has(stopped['run_id']))

    def persisted_counter():
        params=urlencode({'query':'signalflow_trainings_succeeded_total'})
        with urlopen('http://127.0.0.1:9090/api/v1/query?'+params,timeout=5) as response:
            values=json.load(response)['data']['result']
        return bool(values) and float(values[0]['value'][1]) >= before+2
    eventually(persisted_counter)
    evidence={'loki_outage_run_id':offline['run_id'],'full_stack_stopped_run_id':stopped['run_id'],
              'loki_logs_recovered':True,'prometheus_counters_persisted':True,'alloy_restarted_during_outage':True}
    (ROOT/'outputs/observability-validation/outage-verification.json').write_text(json.dumps(evidence,indent=2))
