"""Contrats métier du premier pipeline, réutilisant le chargeur existant."""

from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy
from pathlib import Path
from typing import Any

from signalflow.config import loader
from signalflow.config.loader import schema, yaml_loader
from signalflow.config.loader.paths import resolve_path
from signalflow.config.loader.settings import Limits
from signalflow.tools import ToolRegistry
from .validation import validate_semantics

from .errors import PipelineError

STEPS = (
    "validate_config", "validate_dataset", "validate_model", "train", "evaluate",
    "validate_artifacts",
)
REQUIRED_TOOLS = {"training": "scikit-learn", "experiment_tracking": "mlflow"}


@dataclass(frozen=True)
class RunConfiguration:
    """Documents validés, chemins effectifs et déclarations déjà chargées."""

    base: Path
    loaded: loader.LoadedConfiguration
    training: dict[str, Any]
    dataset: dict[str, Any]
    tracking: dict[str, Any]
    sources: dict[str, str]
    dataset_path: Path | None
    monitoring: dict[str, Any] | None = None

    def snapshot(self) -> dict[str, Any]:
        """Produire la configuration effective sérialisable pour les artefacts.

        Sans paramètres ; retourne une copie profonde des documents.
        Aucune exception attendue ni effet de bord. Le résultat peut être modifié
        indépendamment de la configuration ; il ne contient ni credentials ni URI distante.
        """
        return deepcopy({
            "project": self.loaded.project,
            "tools": [
                {"category": item.category, "provider": item.provider,
                 "enabled": item.enabled,
                 "config": str(item.config) if item.config else None}
                for item in self.loaded.tools
            ],
            "training": self.training, "dataset": self.dataset,
            "tracking": self.tracking, "sources": self.sources,
            "monitoring": self.monitoring,
        })


def _component(
    project: dict[str, Any], name: str, base: Path, limits: Limits,
    override: str | None = None,
) -> tuple[dict[str, Any], Path]:
    reference = project["config"].get(name)
    if reference is None or not reference.get("enabled", True):
        raise PipelineError(f"configuration {name} requise et active pour entraîner")
    path = resolve_path(override or reference["path"], base_dir=base)
    contract_path = resolve_path(reference["schema"], base_dir=base)
    contract = schema.load_schema(contract_path, max_bytes=limits.schema_bytes)
    document = yaml_loader.load_project(path, max_bytes=limits.config_bytes)
    schema.validate(document, contract, schema_path=contract_path).raise_if_invalid(path)
    return document, path


def _check_tools(configuration: loader.LoadedConfiguration) -> None:
    active = {item.category: item.provider for item in configuration.tools if item.enabled}
    metrics = active.pop("metrics", None)
    llm = active.pop("llm", None)
    if active != REQUIRED_TOOLS or metrics not in (None, "local") or llm not in (None, "ollama"):
        raise PipelineError(
            "ce pipeline requiert uniquement training=scikit-learn et "
            "experiment_tracking=mlflow activés, avec metrics=local et llm=ollama facultatifs",
            hint="désactiver les outils non utilisés par ce pipeline minimal",
        )
    if tuple(configuration.project["pipeline"]["steps"]) != STEPS:
        raise PipelineError("la liste des étapes ne correspond pas au pipeline minimal")
    if not configuration.project["pipeline"]["stop_on_failure"]:
        raise PipelineError("le pipeline minimal exige stop_on_failure=true")


def load_run_configuration(
    config_dir: str | Path | None = None, *, registry: ToolRegistry,
    training_override: str | None = None, limits: Limits = loader.DEFAULT_LIMITS,
) -> RunConfiguration:
    """Charger et valider tous les documents requis sans importer de SDK ML.

    Args:
        config_dir: dossier explicite, sinon défaut du chargeur existant.
        registry: registre contenant les adaptateurs explicitement autorisés.
        training_override: référence relative alternative au même schéma training.
        limits: limites des fichiers de configuration et schémas.
    Returns:
        RunConfiguration avec les trois documents métier validés une seule fois.
    Raises:
        ConfigError: erreur de lecture, schéma, YAML ou chemin.
        PipelineError: fractions incohérentes, outils ou étapes non pris en charge.
    Side effects:
        Lectures locales uniquement ; aucun entraînement ou initialisation.
    """
    base = Path(config_dir).expanduser().resolve() if config_dir is not None else loader.CONFIG
    loaded = loader.load_configuration(base, registry=registry, limits=limits)
    _check_tools(loaded)
    training, training_path = _component(loaded.project, "training", base, limits, training_override)
    dataset, dataset_config_path = _component(loaded.project, "dataset", base, limits)
    tracking, tracking_path = _component(loaded.project, "mlops", base, limits)
    monitoring = None
    monitoring_path = None
    if any(tool.category == "metrics" and tool.enabled for tool in loaded.tools):
        monitoring, monitoring_path = _component(loaded.project, "monitoring", base, limits)
    validate_semantics(training, dataset, monitoring)
    # Une éventuelle référence dans tools.yaml ne doit pas contredire le projet.
    # L'override CLI s'applique ensuite à la configuration effective training.
    for tool in loaded.tools:
        component = {"training": "training", "experiment_tracking": "mlops", "metrics": "monitoring"}.get(tool.category)
        if component and tool.config:
            expected = resolve_path(loaded.project["config"][component]["path"], base_dir=base)
            if tool.config != expected:
                raise PipelineError("référence tools.config en conflit avec project.yaml")
    source = dataset["source"]
    dataset_path = resolve_path(source["path"], base_dir=base) if source["type"] == "csv" else None
    return RunConfiguration(
        base, loaded, training, dataset, tracking,
        {"training": str(training_path), "dataset": str(dataset_config_path),
         "tracking": str(tracking_path),
         **({"monitoring": str(monitoring_path)} if monitoring_path else {})}, dataset_path, monitoring,
    )
