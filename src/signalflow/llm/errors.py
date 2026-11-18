"""Erreurs explicites du pipeline Ollama, sans fuite d'URL ni de token."""

from signalflow.config.loader.errors import ConfigError


class LLMError(ConfigError):
    """Erreur générique du pipeline LLM."""

    code = "LLM_ERROR"


class OllamaConfigError(LLMError):
    """Configuration Ollama invalide ou incomplète."""

    code = "OLLAMA_CONFIG_ERROR"


class OllamaRequestError(LLMError):
    """Instance Ollama injoignable, lente ou renvoyant une réponse invalide."""

    code = "OLLAMA_REQUEST_ERROR"


class OllamaModelError(LLMError):
    """Modèle pyfunc indisponible ou impossible à enregistrer dans MLflow."""

    code = "OLLAMA_MODEL_ERROR"


class OllamaTrackingError(LLMError):
    """Échec de publication du suivi ou des traces MLflow."""

    code = "OLLAMA_TRACKING_ERROR"
