"""Fichiers atomiques et verrou interprocessus borné sur Linux/WSL2."""
from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any


@contextmanager
def locked(directory: Path, name: str, timeout: float = 0.25) -> Iterator[None]:
    """Verrouiller une publication locale au plus timeout secondes.

    Crée le dossier et un fichier de verrou. Lève OSError ou TimeoutError ;
    libère et ferme le verrou même si le bloc échoue. Aucun réseau.
    """
    import fcntl

    directory.mkdir(parents=True, exist_ok=True)
    with (directory / name).open('a') as stream:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('observability lock timeout') from None
                time.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def atomic_json(path: Path, value: dict[str, Any]) -> None:
    """Remplacer un document JSON après écriture complète et fsync.

    Le lecteur voit l'ancien ou le nouveau document. Lève OSError/ValueError ;
    supprime le temporaire en cas d'échec, sans effacer le document précédent.
    """
    descriptor, temporary = tempfile.mkstemp(prefix='.snapshot-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, allow_nan=False, separators=(',', ':'))
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)  # Lecture par l'exporteur non privilégié.
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
