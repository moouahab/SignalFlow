"""Comparaison sans classement automatique ni sélection sur le test."""
import math


def compare(jobs, metric):
    rows, warnings = [], []
    conditions = []
    for job in jobs:
        result = job.result or {}
        provenance = result.get('model_package', {}).get('provenance', {})
        value = result.get('metrics', {}).get(metric)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            value = None
            warnings.append({'code': 'METRIC_MISSING', 'run_id': job.id})
        if job.state != 'succeeded':
            warnings.append({'code': 'RUN_NOT_SUCCEEDED', 'run_id': job.id})
        if not all(provenance.get(key) for key in ('dataset_sha256', 'split_sha256', 'environment_sha256')):
            warnings.append({'code': 'PROVENANCE_MISSING', 'run_id': job.id})
        conditions.append((provenance.get('dataset_sha256'), provenance.get('split_sha256'),
                           provenance.get('environment_sha256'),
                           job.snapshot['dataset']['source'].get('target')))
        rows.append({'id': job.id, 'state': job.state, 'metric': value,
                     'metrics': result.get('metrics', {}),
                     'configuration_id': job.configuration_id, 'dataset_id': job.dataset_id,
                     'parameters': job.snapshot['training'], 'provenance': provenance})
    for index, code in enumerate(('DATASET_MISMATCH', 'SPLIT_MISMATCH', 'ENVIRONMENT_MISMATCH', 'TARGET_MISMATCH')):
        if len({item[index] for item in conditions}) > 1:
            warnings.append({'code': code})
    if metric.startswith('test_'):
        warnings.append({'code': 'TEST_METRIC_NOT_FOR_SELECTION'})
    return {'metric': metric, 'runs': rows, 'warnings': warnings,
            'comparable': not warnings}
