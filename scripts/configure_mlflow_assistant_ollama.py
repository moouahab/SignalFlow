#!/usr/bin/env python3
"""Configure l'assistant MLflow pour utiliser l'Ollama Windows depuis WSL.

L'adresse est détectée dans cet ordre :
1. option ``--base-url`` ;
2. variables ``OLLAMA_BASE_URL`` / ``OLLAMA_HOST`` ;
3. passerelle IPv4 par défaut de WSL (``ip -4 route show default``).

Aucune adresse IP n'est codée en dur. Le script vérifie ``GET /api/tags`` et la
présence du modèle demandé avant d'écrire la configuration native MLflow.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Iterable
from urllib.error import URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen

from mlflow.assistant.config import CONFIG_PATH, AssistantConfig, PermissionsConfig

PROVIDER = "ollama"
DEFAULT_MODEL = "qwen3.5:9b"
ENV_VARS = ("OLLAMA_BASE_URL", "OLLAMA_HOST")
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "::"}


def _normalise_url(raw: str) -> str | None:
    """Return a clean http(s) base URL, or None when the value is unusable."""
    value = raw.strip()
    if not value:
        return None
    if "://" not in value:
        value = f"http://{value}"

    try:
        parts = urlparse(value)
        port = parts.port
    except ValueError:
        return None

    host = parts.hostname
    if parts.scheme not in {"http", "https"} or not host:
        return None
    if parts.username or parts.password or parts.query or parts.fragment:
        return None

    if port is not None:
        netloc = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    else:
        netloc = f"[{host}]" if ":" in host else host
    path = parts.path.rstrip("/")
    return urlunparse((parts.scheme, netloc, path, "", "", ""))


def _replace_host(base_url: str, host: str) -> str:
    parts = urlparse(base_url)
    port = parts.port
    bracketed = f"[{host}]" if ":" in host else host
    netloc = f"{bracketed}:{port}" if port is not None else bracketed
    return urlunparse((parts.scheme, netloc, parts.path.rstrip("/"), "", "", ""))


def _wsl_gateways() -> list[str]:
    """Read the IPv4 default gateway, which is the Windows host in WSL NAT mode."""
    ip_bin = shutil.which("ip")
    if ip_bin is None:
        return []
    try:
        result = subprocess.run(
            [ip_bin, "-4", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    gateways: list[str] = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if fields and fields[0] == "default" and "via" in fields:
            candidate = fields[fields.index("via") + 1]
            try:
                address = str(ipaddress.ip_address(candidate))
            except ValueError:
                continue
            if address not in gateways:
                gateways.append(address)
    return gateways


def _candidate_urls(explicit: str | None, gateways: Iterable[str]) -> list[str]:
    """Build an ordered, de-duplicated list of Ollama base URLs to probe."""
    raw_values: list[str] = []
    if explicit:
        raw_values.append(explicit)
    for name in ENV_VARS:
        raw_values.extend(os.environ.get(name, "").split(","))

    candidates: list[str] = []

    def add(url: str | None) -> None:
        if url and url not in candidates:
            candidates.append(url)

    for raw in raw_values:
        url = _normalise_url(raw)
        add(url)
        if url is None:
            continue
        host = (urlparse(url).hostname or "").lower()
        if host in LOOPBACK_HOSTS or host.startswith("127."):
            for gateway in gateways:
                add(_replace_host(url, gateway))

    for gateway in gateways:
        add(f"http://{gateway}:11434")

    return candidates


def _probe(base_url: str, model: str, timeout: float) -> list[str]:
    """Check /api/tags and fail when the requested model is absent."""
    request = Request(f"{base_url}/api/tags", headers={"Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        payload = json.load(response)

    models = {
        entry.get("model") or entry.get("name")
        for entry in payload.get("models", [])
        if isinstance(entry, dict)
    }
    models.discard(None)
    available = sorted(str(name) for name in models)
    if model not in available:
        raise RuntimeError(
            f"modèle '{model}' absent; disponibles: {', '.join(available) or 'aucun'}"
        )
    return available


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        help="URL Ollama explicite (utilisée en priorité ; évite l'auto-détection)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"modèle attendu ({DEFAULT_MODEL})")
    parser.add_argument("--timeout", type=float, default=3.0, help="timeout HTTP en secondes")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="détecter et valider sans écrire la configuration",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    gateways = _wsl_gateways()
    candidates = _candidate_urls(args.base_url, gateways)

    if not candidates:
        print(
            "Aucune URL Ollama détectée. Définissez OLLAMA_BASE_URL ou utilisez --base-url.",
            file=sys.stderr,
        )
        return 2

    failures: list[str] = []
    selected: str | None = None
    for url in candidates:
        try:
            models = _probe(url, args.model, args.timeout)
        except (OSError, URLError, ValueError, RuntimeError) as exc:
            failures.append(f"  - {url} : {exc}")
            continue
        selected = url
        break

    if selected is None:
        print("Aucune instance Ollama joignable avec le modèle demandé :", file=sys.stderr)
        for failure in failures:
            print(failure, file=sys.stderr)
        return 2

    config = AssistantConfig.load()
    permissions = PermissionsConfig(
        allow_edit_files=False,
        allow_read_docs=True,
        full_access=False,
    )
    config.set_provider(PROVIDER, args.model, permissions=permissions, base_url=selected)

    if args.dry_run:
        print(f"Ollama joignable : {selected}")
        print(f"Modèles détectés : {', '.join(models)}")
        print("Dry-run : configuration non écrite.")
        return 0

    config.save()
    print(f"Ollama joignable : {selected}")
    print(f"Modèle sélectionné : {args.model}")
    print(f"Configuration Assistant écrite : {CONFIG_PATH}")
    print("Permissions : lecture des docs uniquement, pas de modification de fichiers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
