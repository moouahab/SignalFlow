"""Commandes locales d’entraînement et de benchmark ; aide sans SDK ML."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from signalflow.config.loader.errors import ConfigError


def main(argv: Sequence[str] | None = None) -> int:
    """Exécuter la CLI SignalFlow.

    Args:
        argv: arguments sans le nom du programme, sinon sys.argv.
    Returns:
        0 pour succès, 1 pour erreur métier, 130 pour interruption.
    Raises:
        SystemExit: aide ou syntaxe d'arguments invalide, gérées par argparse.
    Side effects:
        train ou benchmark écrit le rapport JSON sur stdout et les erreurs
        typées sur stderr. --help ne charge aucune dépendance ML optionnelle.
    """
    parser = argparse.ArgumentParser(prog="signalflow", description="Pipeline ML local reproductible")
    commands = parser.add_subparsers(dest="command", required=True)
    train = commands.add_parser("train", help="entraîner et suivre dans MLflow local")
    train.add_argument("--config-dir", help="dossier contenant project.yaml")
    train.add_argument("--training", help="autre fichier training, relatif au dossier config")
    train.add_argument("--output-dir", required=True, help="dossier des runs et du suivi MLflow local")
    benchmark = commands.add_parser("benchmark", help="répétitions comparables en processus neufs")
    benchmark.add_argument("--config-dir", required=True)
    benchmark.add_argument("--output-dir", required=True)
    benchmark.add_argument("--benchmark", default="./benchmark.yaml", help="fichier relatif au dossier config")
    benchmark.add_argument("--baseline", help="rapport benchmark.json historique à comparer")
    args = parser.parse_args(argv)

    from signalflow.ml.runner import run_training

    try:
        if args.command == "benchmark":
            from signalflow.benchmarking import run_benchmark
            report = run_benchmark(args.config_dir, output_dir=args.output_dir,
                                   benchmark_reference=args.benchmark, baseline=args.baseline)
        else:
            report = run_training(args.config_dir, output_dir=args.output_dir, training_override=args.training)
    except ConfigError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("exécution interrompue", file=sys.stderr)
        return 130
    print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))

    return 0
