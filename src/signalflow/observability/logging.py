"""Événements JSON à vocabulaire fermé, sans exception brute ni contenu utilisateur."""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

from .storage import locked


class JsonEvents:
    """Journal partagé avec rotation sérialisée entre entraînements concurrents."""

    def __init__(self, directory: Path, *, max_bytes: int = 10485760, backups: int = 3) -> None:
        """Configurer destination et rétention bornée ; aucune ouverture au constructeur."""
        self.directory, self.max_bytes, self.backups = directory, max_bytes, backups

    def write(self, event: str, execution_id: str, *, component: str = 'training',
              stage: str = '', level: str = 'INFO', run_id: str | None = None) -> None:
        """Écrire une ligne UTC et faire tourner le journal sous verrou.

        Les arguments viennent du code, jamais du texte de configuration/dataset ou
        d'une exception. Lève OSError/TimeoutError ; aucun réseau ni logger global.
        """
        document = {'timestamp': datetime.now(timezone.utc).isoformat(), 'level': level,
                    'component': component, 'stage': stage, 'event': event,
                    'execution_id': execution_id, 'run_id': run_id, 'message': event.replace('_', ' ')}
        line = json.dumps(document, ensure_ascii=True) + '\n'
        with locked(self.directory, '.logs.lock'):
            path = self.directory / 'events.jsonl'
            if path.exists() and path.stat().st_size + len(line) > self.max_bytes:
                for index in range(self.backups, 0, -1):
                    source = path if index == 1 else self.directory / f'events.jsonl.{index - 1}'
                    if source.exists():
                        source.replace(self.directory / f'events.jsonl.{index}')
            with path.open('a', encoding='utf-8') as stream:
                stream.write(line)
            path.chmod(0o644)  # Lecture Alloy non privilégié ; événements sans données sensibles.
