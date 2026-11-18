"""Création d'un jeton local : la valeur brute n'est affichée qu'une fois."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import os
import secrets

from signalflow.persistence.repositories import TokenRepository, UserRepository
from signalflow.persistence.session import create_session_factory, session_scope

from .auth.identity import hash_token
from .auth.policy import ROLES


def main() -> None:
    """Créer un utilisateur et un jeton dans la base applicative dédiée."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--subject', required=True, help='identifiant logique de l operateur')
    parser.add_argument('--role', choices=sorted(ROLES), default='admin')
    parser.add_argument('--label', default='local', help='etiquette non secrete du jeton')
    parser.add_argument('--ttl-days', type=int, default=30,
                        help='validite du jeton (1 a 365 jours, defaut 30)')
    parser.add_argument('--database-url', default=os.environ.get('SIGNALFLOW_DATABASE_URL'))
    args = parser.parse_args()
    if not 1 <= args.ttl_days <= 365:
        parser.error('--ttl-days doit etre compris entre 1 et 365')
    if not args.database_url:
        parser.error('SIGNALFLOW_DATABASE_URL ou --database-url est requis')
    token = secrets.token_urlsafe(32)
    with session_scope(create_session_factory(args.database_url)) as session:
        users = UserRepository(session)
        user = users.ensure(args.subject, args.role, args.subject)
        expiration = datetime.now(timezone.utc) + timedelta(days=args.ttl_days)
        TokenRepository(session).create(user.id, hash_token(token), args.label,
                                        expires_at=expiration)
    print(token)


if __name__ == '__main__':
    main()
