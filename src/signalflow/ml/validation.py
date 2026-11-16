"""Validation métier commune et schémas embarqués pour les appels sans YAML."""
from __future__ import annotations

from collections.abc import Mapping
from importlib.resources import files
import json
import math
from pathlib import Path
from typing import Any

from signalflow.config.loader import schema
from signalflow.observability.session import destination
from .contracts import TrainingSpec
from .errors import PipelineError

COMPONENTS = {"training": "training", "dataset": "dataset", "tracking": "mlops", "monitoring": "monitoring"}


def validate_semantics(training: dict[str, Any], dataset: dict[str, Any],
                       monitoring: dict[str, Any] | None) -> None:
    """Vérifier les relations entre champs après JSON Schema, sans SDK ni écriture.

    Lève PipelineError pour nombres non finis, partitions ou dimensions incohérentes ;
    la validation de destination peut lever ObservabilityConfigError.
    """
    if monitoring is not None:
        if not math.isfinite(monitoring["interval_seconds"]):
            raise PipelineError("la période d'échantillonnage doit être finie")
        if monitoring.get("prometheus", {}).get("enabled") or monitoring.get("logging", {}).get("enabled"):
            destination(monitoring)
    split = dataset["split"]
    if not all(math.isfinite(value) for value in (
        training["model"]["parameters"]["C"], split["validation"], split["test"],
    )):
        raise PipelineError("les paramètres numériques doivent être finis")
    if split["validation"] + split["test"] >= 1:
        raise PipelineError("validation + test doit être strictement inférieur à 1")
    source = dataset["source"]
    if source["type"] == "synthetic":
        if (source["n_informative"] + source["n_redundant"] > source["n_features"]
                or 2 ** source["n_informative"] < source["n_classes"]):
            raise PipelineError("dimensions ou classes du dataset synthétique incohérentes")


def validate_training_spec(document: Mapping[str, Any]) -> TrainingSpec:
    """Valider training/dataset/tracking et monitoring facultatif, puis figer une copie.

    Aucun fichier utilisateur, SDK ML ou réseau. Les schémas proviennent du package.
    Lève ConfigValidationError pour la structure, PipelineError pour la sémantique
    ou une valeur non JSON. La résolution d'un CSV appartient au contexte d'exécution.
    """
    if not isinstance(document, Mapping):
        raise PipelineError("la spécification doit être un objet")
    try:
        serialized = json.dumps(dict(document), allow_nan=False, sort_keys=True)
        data = json.loads(serialized)
    except (TypeError, ValueError, OverflowError, RecursionError):
        raise PipelineError("la spécification doit contenir uniquement des valeurs JSON finies") from None
    envelope = {
        "type": "object", "additionalProperties": False,
        "required": ["training", "dataset", "tracking"],
        "properties": {name: {"type": "object"} for name in COMPONENTS},
    }
    envelope["properties"]["monitoring"] = {"type": ["object", "null"]}
    label = Path("<training-spec>")
    schema.validate(data, envelope, schema_path=label).raise_if_invalid(label)
    for name, filename in COMPONENTS.items():
        if name == "monitoring" and data.get(name) is None:
            continue
        resource = files("signalflow.config").joinpath("schemas", f"{filename}.schema.json")
        contract = json.loads(resource.read_text(encoding="utf-8"))
        label = Path(f"<training-spec:{name}>")
        schema.validate(data[name], contract, schema_path=label).raise_if_invalid(label)
    validate_semantics(data["training"], data["dataset"], data.get("monitoring"))
    return TrainingSpec(serialized)
