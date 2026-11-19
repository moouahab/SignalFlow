"""Verrou interprocessus du registre local, partagé par toutes les instances API."""
from contextlib import contextmanager
import fcntl
from pathlib import Path

from ..errors import ApiError


@contextmanager
def registry_lock(settings):
    if not settings.registry_root:
        raise ApiError(503, 'REGISTRY_NOT_CONFIGURED', 'Registre non configuré.')
    root = Path(settings.registry_root).resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    with (root / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield root
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
