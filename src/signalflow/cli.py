"""Commandes locales d’entraînement, benchmark et Ollama ; aide sans SDK ML."""

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
        train, benchmark et ollama écrivent le rapport JSON sur stdout et les
        erreurs typées sur stderr. --help ne charge aucune dépendance ML.
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
    ollama = commands.add_parser("ollama", help="appels Ollama suivis dans MLflow")
    ollama.add_argument("--config-dir", help="dossier contenant project.yaml et llm.yaml")
    ollama.add_argument("--output-dir", help="racine des runs MLflow (requise pour chat/generate)")
    call = ollama.add_mutually_exclusive_group(required=True)
    call.add_argument("--chat", metavar="PROMPT", help="appel /api/chat suivi")
    call.add_argument("--generate", metavar="PROMPT", help="appel /api/generate suivi")
    call.add_argument("--models", action="store_true", help="lister les modèles distants")
    call.add_argument("--health", action="store_true", help="tester l'instance distante")
    ollama.add_argument("--system", help="message système pour --chat")
    ollama.add_argument("--no-register", action="store_true", help="ne pas créer de version de modèle")
    args = parser.parse_args(argv)

    try:
        if args.command == "ollama":
            from signalflow.llm import health, list_models, run_ollama_chat, run_ollama_generate

            if args.models:
                report = list_models(args.config_dir)
            elif args.health:
                report = health(args.config_dir)
            else:
                if not args.output_dir:
                    parser.error("--output-dir est requis pour --chat et --generate")
                common = {
                    "config_dir": args.config_dir, "output_dir": args.output_dir,
                    "prompt": args.chat or args.generate, "options": None,
                    "register": not args.no_register,
                }
                report = (run_ollama_chat(**common, system=args.system) if args.chat is not None
                          else run_ollama_generate(**common))
        elif args.command == "benchmark":
            from signalflow.benchmarking import run_benchmark

            report = run_benchmark(args.config_dir, output_dir=args.output_dir,
                                   benchmark_reference=args.benchmark, baseline=args.baseline)
        else:
            from signalflow.ml.runner import run_training

            report = run_training(args.config_dir, output_dir=args.output_dir, training_override=args.training)
    except ConfigError as error:
        print(str(error), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("exécution interrompue", file=sys.stderr)
        return 130
    print(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False))

    return 0
