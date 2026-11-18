"""Client HTTP d'une instance Ollama distante, sans SDK ni secret persisté."""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from signalflow.tools import ToolDeclaration

from ..errors import OllamaConfigError, OllamaRequestError

MAX_TEXT_CHARS = 65536
MAX_MESSAGES = 50
_ALLOWED_ROLES = frozenset({"system", "user", "assistant", "tool"})


class OllamaClient:
    """Client borné pour /api/chat, /api/generate et /api/tags.

    L'URL provient exclusivement de la configuration validée. Le token eventuel
    est lu dans l'environnement à chaque requete et n'est jamais journalise.
    """

    def __init__(self, settings: dict[str, Any]) -> None:
        """Configurer le client sans effectuer de connexion.

        Args:
            settings: sous-dictionnaire ``ollama`` de llm.yaml valide.
        Returns:
            None.
        Raises:
            OllamaConfigError: réglages absents ou hors bornes.
        Side effects:
            Aucun ; le token n'est lu qu'au moment d'une requête.
        """
        try:
            self.base_url = settings["base_url"].rstrip("/")
            self.model = settings["model"]
            self.options = dict(settings.get("options", {}))
            self.timeout = float(settings.get("timeout_seconds", 60))
            self.max_response_bytes = int(settings.get("max_response_bytes", 1048576))
        except (KeyError, TypeError, ValueError):
            raise OllamaConfigError("paramètres Ollama incomplets") from None
        self.api_key_env = settings.get("api_key_env")

    @classmethod
    def from_declaration(cls, declaration: ToolDeclaration) -> OllamaClient:
        """Construire le client depuis la référence d'une déclaration active.

        Args:
            declaration: sélection ``llm=ollama`` validée par le chargeur.
        Returns:
            Client configuré, sans requête réseau.
        Raises:
            OllamaConfigError: référence absente ou document invalide.
        Side effects:
            Deux lectures locales du fichier llm.yaml et de son schéma.
        """
        from ..config import load_llm_settings

        if declaration.config is None:
            raise OllamaConfigError(
                "référence de configuration Ollama absente", config_path="$.tools.llm.config",
            )
        settings = load_llm_settings(declaration.config)
        return cls(settings["ollama"])

    def _headers(self) -> dict[str, str]:
        """Produire les en-têtes de requête, avec un token lu à l'appel."""
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key_env is not None:
            key = os.environ.get(self.api_key_env)
            if not key:
                raise OllamaConfigError(
                    f"variable d'environnement absente : {self.api_key_env}",
                    config_path="$.ollama.api_key_env",
                )
            headers["Authorization"] = f"Bearer {key}"
        return headers

    def _request(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Appeler l'API Ollama et renvoyer un dictionnaire JSON borné.

        Les erreurs ne contiennent ni l'URL complète ni le corps de réponse :
        un proxy pourrait y inclure des identifiants.
        """
        data = json.dumps(payload, allow_nan=False).encode("utf-8") if payload is not None else None
        request = Request(
            f"{self.base_url}{path}", data=data, headers=self._headers(),
            method="POST" if data is not None else "GET",
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                raw = response.read(self.max_response_bytes + 1)
        except HTTPError as error:
            raise OllamaRequestError(f"Ollama a répondu HTTP {error.code}") from None
        except (URLError, TimeoutError, OSError):
            raise OllamaRequestError("instance Ollama injoignable ou trop lente") from None
        if len(raw) > self.max_response_bytes:
            raise OllamaRequestError("réponse Ollama trop volumineuse")
        try:
            document = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise OllamaRequestError("réponse Ollama invalide") from None
        if not isinstance(document, dict):
            raise OllamaRequestError("réponse Ollama invalide")
        return document

    @staticmethod
    def _validate_messages(messages: Any) -> list[dict[str, str]]:
        """Accepter uniquement des messages de chat textuels et bornés."""
        if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
            raise OllamaRequestError("messages Ollama invalides")
        result = []
        for message in messages:
            if not isinstance(message, dict) or message.get("role") not in _ALLOWED_ROLES:
                raise OllamaRequestError("rôle de message invalide")
            content = message.get("content")
            if not isinstance(content, str) or not content or len(content) > MAX_TEXT_CHARS:
                raise OllamaRequestError("contenu de message invalide")
            result.append({"role": message["role"], "content": content})
        return result

    @staticmethod
    def _validate_prompt(prompt: Any) -> str:
        """Accepter un prompt textuel non vide et borné."""
        if not isinstance(prompt, str) or not prompt or len(prompt) > MAX_TEXT_CHARS:
            raise OllamaRequestError("prompt Ollama invalide")
        return prompt

    def _options(self, overrides: dict[str, Any] | None) -> dict[str, Any]:
        """Fusionner les options par defaut avec celles de l'appel."""
        if overrides is None:
            return dict(self.options)
        if not isinstance(overrides, dict):
            raise OllamaRequestError("options Ollama invalides")
        return {**self.options, **overrides}

    def _normalize(self, payload: dict[str, Any], *, text: Any, latency: float) -> dict[str, Any]:
        """Convertir une réponse Ollama en rapport numérique et textuel borné."""
        if not isinstance(text, str):
            raise OllamaRequestError("réponse Ollama sans texte")
        counts = {}
        for key in ("prompt_eval_count", "eval_count", "total_duration", "prompt_eval_duration", "eval_duration"):
            value = payload.get(key, 0)
            counts[key] = int(value) if isinstance(value, (int, float)) and value >= 0 else 0
        eval_ns = counts["eval_duration"]
        return {
            "model": str(payload.get("model", self.model)),
            "text": text[:MAX_TEXT_CHARS],
            "done": bool(payload.get("done", True)),
            "prompt_eval_count": counts["prompt_eval_count"],
            "eval_count": counts["eval_count"],
            "total_duration_ns": counts["total_duration"],
            "prompt_eval_duration_ns": counts["prompt_eval_duration"],
            "eval_duration_ns": eval_ns,
            "tokens_per_second": round(counts["eval_count"] / (eval_ns / 1e9), 3) if eval_ns else None,
            "latency_seconds": round(latency, 6),
        }

    def tags(self) -> list[str]:
        """Lister les modèles présents sur l'instance distante."""
        payload = self._request("/api/tags")
        models = payload.get("models", [])
        if not isinstance(models, list):
            raise OllamaRequestError("liste de modèles invalide")
        return [str(item["name"]) for item in models if isinstance(item, dict) and "name" in item]

    def health(self) -> bool:
        """Vérifier que l'instance répond, sans lever l'erreur réseau."""
        try:
            self.tags()
        except OllamaRequestError:
            return False
        return True

    def chat(
        self, messages: list[dict[str, str]], *, options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Générer une réponse de chat et renvoyer un rapport borné."""
        payload = {
            "model": self.model,
            "messages": self._validate_messages(messages),
            "options": self._options(options),
            "stream": False,
        }
        started = time.perf_counter()
        response = self._request("/api/chat", payload)
        message = response.get("message")
        text = message.get("content", "") if isinstance(message, dict) else None
        return self._normalize(response, text=text, latency=time.perf_counter() - started)

    def generate(
        self, prompt: str, *, options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Générer une réponse à partir d'un prompt et renvoyer un rapport borné."""
        payload = {
            "model": self.model,
            "prompt": self._validate_prompt(prompt),
            "options": self._options(options),
            "stream": False,
        }
        started = time.perf_counter()
        response = self._request("/api/generate", payload)
        return self._normalize(response, text=response.get("response"), latency=time.perf_counter() - started)
