"""Modèle Ollama au format MLflow pyfunc, sans URI globale imposée."""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .errors import OllamaModelError

try:  # L'import de ce module ne doit pas exiger MLflow.
    from mlflow.pyfunc import PythonModel as _PythonModel
except ImportError:  # pragma: no cover - dépendance optionnelle absente
    class _PythonModel:  # type: ignore[no-redef]
        """Base de remplacement lorsque MLflow n'est pas installé."""


_SETTINGS_KEYS = frozenset({
    "base_url", "model", "api_key_env", "timeout_seconds",
    "max_response_bytes", "options",
})
_OPTION_BOUNDS = {
    "temperature": (0, 2),
    "top_p": (0, 1),
    "top_k": (0, 200),
    "num_predict": (1, 32768),
    "seed": (0, 4294967295),
}
_INTEGER_OPTIONS = frozenset({"top_k", "num_predict", "seed"})
_ALLOWED_OPTIONS = frozenset(_OPTION_BOUNDS) | {"stop"}


def _require_pyfunc() -> None:
    """Refuser explicitement la sauvegarde sans MLflow pyfunc."""
    if _PythonModel.__module__ == __name__:
        raise OllamaModelError("mlflow avec le flavor pyfunc est requis")


def _safe_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """Autoriser uniquement les réglages Ollama publics et sérialisables.

    Un secret d'authentification ne doit jamais être intégré à un modèle MLflow.
    Seul le NOM de la variable d'environnement (api_key_env) est conservé.
    """
    if not isinstance(settings, dict) or set(settings) - _SETTINGS_KEYS:
        raise OllamaModelError("paramètres Ollama non autorisés")

    base_url = settings.get("base_url")
    model = settings.get("model")
    if not isinstance(base_url, str) or not isinstance(model, str):
        raise OllamaModelError("adresse ou modèle Ollama invalide")
    try:
        url = urlsplit(base_url)
        valid_url = (
            url.scheme in {"http", "https"}
            and bool(url.hostname)
            and url.username is None
            and url.password is None
            and not url.query
            and not url.fragment
            and not any(ch.isspace() or ord(ch) < 32 for ch in base_url)
        )
    except ValueError:
        valid_url = False
    if not valid_url:
        raise OllamaModelError("URL Ollama invalide ou contenant des identifiants")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", model):
        raise OllamaModelError("nom du modèle Ollama invalide")

    api_key_env = settings.get("api_key_env")
    if api_key_env is not None and (
        not isinstance(api_key_env, str)
        or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", api_key_env)
    ):
        raise OllamaModelError("api_key_env doit être un nom de variable d'environnement")

    timeout = settings.get("timeout_seconds", 60)
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 1 <= timeout <= 300):
        raise OllamaModelError("timeout_seconds hors limites")
    max_bytes = settings.get("max_response_bytes", 1048576)
    if type(max_bytes) is not int or not 1024 <= max_bytes <= 8388608:
        raise OllamaModelError("max_response_bytes hors limites")

    options = settings.get("options", {})
    if not isinstance(options, dict) or set(options) - _ALLOWED_OPTIONS:
        raise OllamaModelError("options Ollama non autorisées")
    for key, value in options.items():
        if key == "stop":
            if (not isinstance(value, list) or not 1 <= len(value) <= 8
                    or any(not isinstance(item, str) or not 1 <= len(item) <= 128
                           for item in value)):
                raise OllamaModelError("option stop invalide")
            continue
        minimum, maximum = _OPTION_BOUNDS[key]
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or (key in _INTEGER_OPTIONS and type(value) is not int)
                or not minimum <= value <= maximum):
            raise OllamaModelError(f"option Ollama invalide : {key}")

    # Copie profonde : une modification ultérieure du dictionnaire d'origine
    # ne doit pas modifier les paramètres déjà enregistrés dans le modèle.
    sanitized = deepcopy(settings)
    try:
        json.dumps(sanitized, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        raise OllamaModelError("paramètres Ollama non sérialisables") from None
    return sanitized


class OllamaPyFunc(_PythonModel):
    """Générer une réponse Ollama pour chaque invite fournie."""

    # Mécanisme officiel de mlflow.pyfunc.PythonModel : évite l'inférence et la
    # validation de signature fondées sur les annotations de predict. Celles-ci
    # ne peuvent pas décrire les formats acceptés (str, liste, DataFrame).
    _skip_type_hint_validation = True

    def __init__(self, settings: dict[str, Any]) -> None:
        self.settings = _safe_settings(settings)

    @staticmethod
    def _prompts(model_input: Any) -> list[str]:
        """Accepter str, list, tuple, tableau ou DataFrame avec `prompt`."""
        if isinstance(model_input, str):
            values = [model_input]
        elif isinstance(model_input, (list, tuple)):
            values = list(model_input)
        elif (hasattr(model_input, "columns")
              and "prompt" in getattr(model_input, "columns", ())):
            values = list(model_input["prompt"])
        elif hasattr(model_input, "tolist"):
            converted = model_input.tolist()
            values = converted if isinstance(converted, list) else [converted]
        else:
            raise OllamaModelError("entrée pyfunc incompréhensible")

        if not values:
            raise OllamaModelError("aucune invite fournie")
        prompts: list[str] = []
        for value in values:
            if isinstance(value, (list, tuple)) and len(value) == 1:
                value = value[0]
            if not isinstance(value, str) or not value:
                raise OllamaModelError("invite invalide")
            prompts.append(value)
        return prompts

    # Intentionnel : model_input reste non annoté (voir _skip_type_hint_validation).
    def predict(self, context, model_input, params=None):  # type: ignore[no-untyped-def]
        """Retourner une liste de réponses, dans l'ordre des invites."""
        from .adapters.ollama import OllamaClient

        prompts = self._prompts(model_input)
        if params is not None and not isinstance(params, dict):
            raise OllamaModelError("paramètres de prédiction invalides")
        allowed = ("temperature", "top_p", "top_k", "num_predict", "seed")
        options = {key: params[key] for key in allowed if key in params} if params else None
        client = OllamaClient(self.settings)
        return [client.generate(prompt, options=options)["text"] for prompt in prompts]


def save_model(destination: Path, settings: dict[str, Any]) -> Path:
    """Sauvegarder le modèle pyfunc et ses seuls réglages non sensibles."""
    _require_pyfunc()
    public_settings = _safe_settings(settings)  # Avant toute écriture.
    try:
        import mlflow.pyfunc

        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        mlflow.pyfunc.save_model(
            path=str(destination), python_model=OllamaPyFunc(public_settings),
        )
        (destination / "ollama_settings.json").write_text(
            json.dumps(public_settings, ensure_ascii=False, allow_nan=False, indent=2) + "\n",
            encoding="utf-8",
        )
    except OllamaModelError:
        raise
    except Exception:
        # Ne pas exposer de détails d'erreurs internes potentiellement sensibles.
        raise OllamaModelError("impossible d'enregistrer le modèle pyfunc Ollama") from None
    return destination


def log_model(client: Any, run_id: str, model_dir: Path) -> str:
    """Publier un modèle local comme artefact `model` d'un run MLflow."""
    try:
        client.log_artifacts(run_id, str(model_dir), artifact_path="model")
        root = client.get_run(run_id).info.artifact_uri
        if not isinstance(root, str) or not root:
            raise ValueError("URI d'artefact absente")
    except Exception:
        raise OllamaModelError("impossible de publier le modèle dans MLflow") from None
    return f"{root.rstrip('/')}/model"


def register_model(
    client: Any, run_id: str, source: str, name: str, *, alias: str | None = None,
) -> str:
    """Créer une version dans le registre MLflow sans supprimer l'existant."""
    try:
        from mlflow.exceptions import ErrorCode, MlflowException

        try:
            client.create_registered_model(name)
        except MlflowException as error:
            code = getattr(error, "error_code", None)
            if code not in (ErrorCode.RESOURCE_ALREADY_EXISTS, "RESOURCE_ALREADY_EXISTS"):
                raise
        version = client.create_model_version(name, source=source, run_id=run_id)
        if alias:
            client.set_registered_model_alias(name, alias, version.version)
    except Exception:
        raise OllamaModelError("impossible d'enregistrer la version du modèle") from None
    return str(version.version)
