"""Modèle Ollama au format MLflow pyfunc, sans URI globale imposée."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import OllamaModelError


try:  # MLflow reste optionnel : l'import du module n'exige pas le SDK.
    from mlflow.pyfunc import PythonModel as _PythonModel
except ImportError:  # pragma: no cover - dépendance absente
    class _PythonModel:  # type: ignore[no-redef]
        """Base fictive, remplacée par un contrôle explicite dans save_model."""


def _require_pyfunc() -> None:
    """Vérifier que le flavor pyfunc est disponible avant de l'utiliser."""
    if _PythonModel.__module__ == __name__:
        raise OllamaModelError("mlflow avec le flavor pyfunc est requis")


class OllamaPyFunc(_PythonModel):
    """Prédictor pyfunc : une génération Ollama par invite reçue."""

    def __init__(self, settings: dict[str, Any]) -> None:
        """Conserver les réglages Ollama sans secret en clair."""
        self.settings = dict(settings)

    @staticmethod
    def _prompts(model_input: Any) -> list[str]:
        """Accepter chaîne, séquence, tableau ou DataFrame avec colonne prompt."""
        if isinstance(model_input, str):
            values = [model_input]
        elif isinstance(model_input, (list, tuple)):
            values = list(model_input)
        elif hasattr(model_input, "columns") and "prompt" in getattr(model_input, "columns", []):
            values = list(model_input["prompt"])
        elif hasattr(model_input, "tolist"):
            values = list(model_input.tolist())
        else:
            raise OllamaModelError("entrée pyfunc incompréhensible")
        if not values:
            raise OllamaModelError("aucune invite fournie")
        prompts = []
        for value in values:
            if isinstance(value, (list, tuple)) and len(value) == 1:
                value = value[0]
            if not isinstance(value, str) or not value:
                raise OllamaModelError("invite invalide")
            prompts.append(value)
        return prompts

    # Sans annotation de type : MLflow n'infere pas de signature erronee ici.
    def predict(self, context, model_input, params=None):  # type: ignore[no-untyped-def]
        """Générer une réponse textuelle pour chaque invite."""
        from .adapters.ollama import OllamaClient

        client = OllamaClient(self.settings)
        options = None
        if params:
            options = {key: params[key] for key in ("temperature", "top_p", "top_k", "num_predict", "seed")
                       if key in params}
        return [client.generate(prompt, options=options)["text"] for prompt in self._prompts(model_input)]


def save_model(destination: Path, settings: dict[str, Any]) -> Path:
    """Écrire un modèle pyfunc autonome dans un dossier local.

    Args:
        destination: dossier de destination, créé s'il manque.
        settings: sous-dictionnaire ``ollama`` validé, sans token en clair.
    Returns:
        Chemin du dossier contenant MLmodel et python_model.pkl.
    Raises:
        OllamaModelError: mlflow/pyfunc indisponible ou écriture impossible.
    Side effects:
        Écrit le modèle sur disque et un fichier d'audit lisible.
    """
    try:
        _require_pyfunc()
        import mlflow.pyfunc

        destination.mkdir(parents=True, exist_ok=True)
        mlflow.pyfunc.save_model(path=str(destination), python_model=OllamaPyFunc(settings))
        (destination / "ollama_settings.json").write_text(
            json.dumps(settings, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8",
        )
    except OllamaModelError:
        raise
    except Exception:
        raise OllamaModelError("impossible d'enregistrer le modèle pyfunc Ollama") from None
    return destination


def log_model(client: Any, run_id: str, model_dir: Path) -> str:
    """Copier le modèle sauvegardé dans les artefacts d'un run MLflow.

    Args:
        client: MlflowClient déjà configuré.
        run_id: run auquel rattacher le modèle.
        model_dir: dossier produit par save_model.
    Returns:
        URI de l'artefact `model` utilisable comme source du Model Registry.
    Raises:
        OllamaModelError: copie impossible.
    Side effects:
        Écrit les fichiers du modèle dans le stockage d'artefacts MLflow.
    """
    try:
        client.log_artifacts(run_id, str(model_dir), artifact_path="model")
        root = client.get_run(run_id).info.artifact_uri
    except Exception:
        raise OllamaModelError("impossible de publier le modèle dans MLflow") from None
    return f"{root.rstrip('/')}/model"


def register_model(
    client: Any, run_id: str, source: str, name: str, *, alias: str | None = None,
) -> str:
    """Créer ou réutiliser un modèle enregistré et publier une version.

    Args:
        client: MlflowClient déjà configuré.
        run_id: run d'origine de la version.
        source: URI renvoyée par log_model.
        name: nom du modèle enregistré.
        alias: alias optionnel à positionner sur la nouvelle version.
    Returns:
        Numéro de version créé.
    Raises:
        OllamaModelError: registre indisponible ou version impossible.
    Side effects:
        Écrit dans le Model Registry MLflow ; aucune suppression.
    """
    try:
        from mlflow.exceptions import ErrorCode, MlflowException

        try:
            client.create_registered_model(name)
        except MlflowException as error:
            # MLflow 3.18 expose parfois le code sous forme de chaîne, parfois
            # sous forme de valeur ErrorCode : accepter les deux représentations.
            code = getattr(error, "error_code", None)
            if code not in (ErrorCode.RESOURCE_ALREADY_EXISTS, "RESOURCE_ALREADY_EXISTS"):
                raise
        version = client.create_model_version(name, source=source, run_id=run_id)
        if alias:
            client.set_registered_model_alias(name, alias, version.version)
    except Exception:
        raise OllamaModelError("impossible d'enregistrer la version du modèle") from None
    return version.version
