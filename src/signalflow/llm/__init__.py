"""Intégration Ollama : configuration, suivi MLflow et modèle pyfunc.

Les modules lourds (mlflow.pyfunc) restent importés à l'usage ; importer ce
paquet n'ouvre aucune connexion réseau et ne charge aucun SDK ML.
"""

from .config import LLMConfiguration, load_llm_configuration, load_llm_settings
from .errors import (
    LLMError,
    OllamaConfigError,
    OllamaModelError,
    OllamaRequestError,
    OllamaTrackingError,
)
from .runner import health, list_models, run_ollama_chat, run_ollama_generate

__all__ = [
    "LLMConfiguration", "load_llm_configuration", "load_llm_settings",
    "LLMError", "OllamaConfigError", "OllamaModelError", "OllamaRequestError", "OllamaTrackingError",
    "health", "list_models", "run_ollama_chat", "run_ollama_generate",
]
