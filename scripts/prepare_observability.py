"""Préparer les permissions hôte et le secret Grafana, sans écraser de fichier existant."""
from pathlib import Path
import os
import secrets


def main() -> None:
    """Créer les sorties sous le dépôt et un secret aléatoire lisible via Docker secret."""
    root = Path(__file__).resolve().parents[1]
    shared = root / 'outputs/observability'
    if shared.is_symlink():
        raise RuntimeError('Le dossier partagé ne doit pas être un lien symbolique.')
    shared.mkdir(parents=True, exist_ok=True)
    shared.chmod(0o755)  # Les lecteurs non-root des conteneurs traversent ce dossier.
    directory = root / 'secrets'
    if directory.is_symlink():
        raise RuntimeError('Le dossier secrets ne doit pas être un lien symbolique.')
    directory.mkdir(mode=0o700, exist_ok=True)
    directory.chmod(0o700)
    path = directory / 'grafana_password'
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        print('Préparation conservée ; secret existant inchangé.')
    else:
        os.fchmod(descriptor, 0o644)  # Parent 0700 sur l'hôte ; secret monté seul dans Grafana.
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(secrets.token_urlsafe(32) + '\n')
        print('Préparation terminée ; secret Grafana dans secrets/grafana_password (non affiché).')


if __name__ == '__main__':
    main()
