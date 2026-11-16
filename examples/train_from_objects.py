"""Entraîner Iris avec des objets Python, sans charger ni créer de configuration YAML."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from signalflow.ml.contracts import ExecutionContext
from signalflow.ml.runner import run_training_spec
from signalflow.ml.validation import validate_training_spec


def main() -> None:
    """Valider les paramètres en mémoire, entraîner et afficher le rapport JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    spec = validate_training_spec({
        'training': {
            'schema_version': '0.0.1', 'seed': 42,
            'model': {'type': 'logistic_regression', 'parameters': {'C': 1.0, 'max_iter': 1000}},
        },
        'dataset': {
            'schema_version': '0.0.1', 'source': {'type': 'builtin', 'name': 'iris'},
            'split': {'validation': 0.2, 'test': 0.2, 'stratify': True},
        },
        'tracking': {
            'schema_version': '0.0.1', 'experiment_name': 'python-api', 'run_name': 'iris',
        },
    })
    report = run_training_spec(spec, ExecutionContext(output_dir=args.output_dir))
    print(json.dumps(report.to_dict(), indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
