"""Sonde du seul processus courant ; métadonnées système séparées."""

from dataclasses import dataclass
import os
from pathlib import Path
import platform
from typing import Any, Protocol

@dataclass(frozen=True)
class Reading:
    """RSS en octets et CPU user+system cumulé, hors processus enfants."""
    rss_bytes: int
    cpu_seconds: float


class ResourceProbe(Protocol):
    """Source injectable de mesures CPU/RAM, sans connaître le pipeline."""
    def read(self) -> Reading:
        """Retourner les compteurs courants ; peut lever une erreur d'accès OS."""
        ...
    def environment(self) -> dict[str, Any]:
        """Retourner les ressources visibles du système, sans secrets ni environnement shell."""
        ...


class PsutilProbe:
    """Sonde locale ; psutil n'est importé qu'à l'activation du fournisseur."""
    def __init__(self) -> None:
        """Créer la sonde courante, sans paramètre ni retour.
        Importe psutil ; ImportError si l'extra monitoring est absent. Aucun réseau.
        """
        import psutil
        self._psutil = psutil
        self._process = psutil.Process(os.getpid())

    def read(self) -> Reading:
        """Lire RSS et user+system du processus ; retourne Reading.
        Aucun paramètre. Lectures OS seulement ; erreurs psutil propagées.
        Les threads sont inclus, les enfants exclus ; la RAM partagée compte dans RSS.
        """
        cpu = self._process.cpu_times()
        return Reading(self._process.memory_info().rss, cpu.user + cpu.system)

    def environment(self) -> dict[str, Any]:
        """Décrire le matériel visible et la mémoire système au démarrage.
        Aucun paramètre. Retourne un dictionnaire JSON, sans identifiants ou secrets.
        Lectures OS ; erreurs psutil propagées, affinité indisponible notée null.
        Les quotas cgroup ne sont pas mesurés : les totaux visibles ne sont pas une
        garantie des ressources réellement allouées dans un conteneur.
        """
        memory = self._psutil.virtual_memory()
        try:
            affinity = len(self._process.cpu_affinity())
        except (AttributeError, self._psutil.Error):
            affinity = None
        model = platform.processor()
        try:
            with Path('/proc/cpuinfo').open() as stream:
                for line in stream.read(65536).splitlines():
                    if line.startswith('model name'):
                        model = line.partition(':')[2].strip()
                        break
        except OSError:
            pass
        return {
            "platform": platform.platform(), "machine": platform.machine(), "cpu_model": model,
            "logical_cpus_visible": self._psutil.cpu_count(),
            "physical_cores_visible": self._psutil.cpu_count(logical=False),
            "process_affinity_cpu_count": affinity,
            "system_memory_total_bytes": memory.total,
            "system_memory_available_bytes_at_start": memory.available,
            "psutil_version": self._psutil.__version__,
            "resource_scope": "current_process_threads_no_children",
            "container_quotas_measured": False,
        }
