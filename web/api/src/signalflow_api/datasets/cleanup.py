"""Collecte hors ligne : arrêter TOUS les producteurs avant --apply."""
import argparse
import os
import time

from sqlalchemy import select
from signalflow.persistence.models import DatasetVersion
from signalflow.persistence.session import create_session_factory, session_scope
from .storage import Storage


def collect(factory, storage, apply=False, min_age_seconds=86400):
    """Sans écrivains actifs ; préserve toutes les références et les fichiers récents."""
    with session_scope(factory) as session:
        referenced = set(session.scalars(select(DatasetVersion.id)))
    candidates = []
    for path in storage.root.iterdir():
        if (path.is_symlink() or not path.is_file() or path.name in referenced
                or time.time() - path.stat().st_mtime < min_age_seconds):
            continue
        try:
            storage.path(path.name)
        except ValueError:
            continue
        candidates.append(path.name)
        if apply:
            storage.discard(path.name)
    return candidates


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--writers-stopped', action='store_true')
    args = parser.parse_args()
    if args.apply and not args.writers_stopped:
        parser.error('--apply nécessite --writers-stopped')
    factory = create_session_factory(os.environ['SIGNALFLOW_DATABASE_URL'])
    storage = Storage(os.environ['SIGNALFLOW_DATASET_ROOT'])
    print('\n'.join(collect(factory, storage, apply=args.apply)))


if __name__ == '__main__':
    main()
