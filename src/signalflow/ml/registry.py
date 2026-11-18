"""Enregistrement des adaptateurs ML et monitoring, sans import de SDK."""

from signalflow.tools import ToolDeclaration, ToolRegistry


def _sklearn(declaration: ToolDeclaration) -> object:
    from .adapters.sklearn import SklearnTrainer
    return SklearnTrainer()


def _mlflow(declaration: ToolDeclaration) -> object:
    from .adapters.mlflow import MLflowTracker
    return MLflowTracker()


def _ollama(declaration: ToolDeclaration) -> object:
    from signalflow.llm.adapters.ollama import OllamaClient
    return OllamaClient.from_declaration(declaration)


def _local(declaration: ToolDeclaration) -> object:
    from signalflow.monitoring.local import LocalMonitor
    return LocalMonitor()


def create_registry() -> ToolRegistry:
    """Construire un registre avec sklearn, MLflow, monitoring local et Ollama.

    Sans paramètres. Retourne un nouveau ToolRegistry, sans modifier de singleton.
    Aucune exception attendue. N'importe aucun SDK et n'appelle aucune factory.
    Les dépendances sont chargées uniquement à initialize() pour un outil actif.
    """
    registry = ToolRegistry()
    registry.register("training", "scikit-learn", _sklearn)
    registry.register("experiment_tracking", "mlflow", _mlflow)
    registry.register("metrics", "local", _local)
    registry.register("llm", "ollama", _ollama)
    return registry
