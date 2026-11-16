"""Agrégation de répétitions et signalement prudent des régressions potentielles."""

from __future__ import annotations

import statistics
from typing import Any


REGRESSION_METRICS = (
    "stage_model_fit_seconds", "pipeline_total_seconds", "command_wall_seconds",
    "process_rss_sampled_max_bytes", "process_cpu_seconds",
)


def summarize(values: list[float]) -> dict[str, float | int]:
    """Calculer moyenne, médiane, écart-type échantillon, min/max et CV.

    Args: values: au moins deux mesures finies, comparables.
    Retourne un dictionnaire ; ValueError si moins de deux mesures. Aucun effet.
    CV=0 lorsque toutes les valeurs sont nulles ; pas d'intervalle de confiance.
    """
    if len(values) < 2:
        raise ValueError("au moins deux répétitions sont nécessaires")
    mean = statistics.mean(values)
    stdev = statistics.stdev(values)
    return {"count": len(values), "mean": mean, "median": statistics.median(values),
            "stdev": stdev, "min": min(values), "max": max(values),
            "cv_percent": 100 * stdev / mean if mean else 0.0}


def aggregate(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Agréger les mesures d'un groupe de runs réussis et comparables.

    Args: runs: rapports normalisés contenant metrics.
    Retourne une statistique par métrique. ValueError si moins de deux runs ou
    métriques différentes. Aucun effet de bord ; ne filtre pas silencieusement.
    """
    if len(runs) < 2:
        raise ValueError("au moins deux répétitions sont nécessaires")
    keys = set(runs[0]["metrics"])
    if any(set(run["metrics"]) != keys for run in runs):
        raise ValueError("mesures différentes entre répétitions")
    return {key: summarize([run["metrics"][key] for run in runs]) for key in sorted(keys)}


def compare(reference: dict[str, Any], candidate: dict[str, Any], threshold: float) -> dict[str, Any]:
    """Signaler les hausses au-delà du seuil ET de deux écarts-types observés.

    Args: reference/candidate: statistiques agrégées ; threshold: hausse en %.
    Retourne les ratios et drapeaux potential_regression ; jamais une preuve
    statistique ni une conclusion d'optimisation. Aucun effet, erreurs de clés
    propagées si l'appelant fournit des agrégats incomplets.
    """
    comparison = {}
    for metric in REGRESSION_METRICS:
        baseline, current = reference[metric], candidate[metric]
        difference = current["mean"] - baseline["mean"]
        noise = 2 * max(baseline["stdev"], current["stdev"])
        change = 100 * difference / baseline["mean"] if baseline["mean"] > 0 else None
        comparison[metric] = {
            "change_percent": change, "noise_guard_absolute": noise,
            "potential_regression": change is not None and change > threshold and difference > noise,
        }
    return comparison
