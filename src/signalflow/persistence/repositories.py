"""Dépôts transactionnels ; aucune suppression ni mise à jour de version."""
from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .errors import AlreadyExistsError, ForbiddenError, NotFoundError, VersionConflict
from .models import ConfigurationVersion, Membership, Project, Token, User

VALID_ROLES = frozenset({"viewer", "editor", "admin"})


class UserRepository:
    """Identités locales et jetons hachés."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def ensure(self, subject: str, role: str, display_name: str | None = None) -> User:
        """Créer ou retourner une identité ; le rôle est fourni par l'opérateur."""
        if role not in VALID_ROLES:
            raise ValueError("rôle inconnu")
        user = self.session.scalar(select(User).where(User.subject == subject))
        if user is not None:
            return user
        user = User(subject=subject, display_name=display_name or subject, role=role)
        self.session.add(user)
        self.session.flush()
        return user

    def get(self, user_id: str) -> User | None:
        return self.session.get(User, user_id)

    def by_subject(self, subject: str) -> User | None:
        return self.session.scalar(select(User).where(User.subject == subject))


class TokenRepository:
    """Résolution de jetons sans conserver la valeur brute."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, user_id: str, token_hash: str, label: str) -> Token:
        """Enregistrer une empreinte ; l'appelant a déjà généré le jeton brut."""
        if len(token_hash) != 64:
            raise ValueError("empreinte de jeton invalide")
        token = Token(user_id=user_id, token_hash=token_hash, label=label or "local")
        self.session.add(token)
        self.session.flush()
        return token

    def find(self, token_hash: str) -> dict[str, Any] | None:
        """Retourner subject/rôle/utilisateur d'un jeton actif, sinon None."""
        row = self.session.execute(
            select(Token, User).join(User, Token.user_id == User.id).where(
                Token.token_hash == token_hash, Token.revoked_at.is_(None)
            )
        ).one_or_none()
        if row is None:
            return None
        token, user = row
        return {"user_id": user.id, "subject": user.subject, "role": user.role, "token_id": token.id}

    def revoke(self, token_hash: str) -> bool:
        """Révoquer un jeton ; retourner False s'il était absent ou déjà révoqué."""
        from datetime import datetime, timezone

        token = self.session.scalar(select(Token).where(
            Token.token_hash == token_hash, Token.revoked_at.is_(None)
        ))
        if token is None:
            return False
        token.revoked_at = datetime.now(timezone.utc)
        self.session.flush()
        return True


class ProjectRepository:
    """Projets, appartenance et contrôle de visibilité."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, key: str, name: str, description: str, creator: User) -> Project:
        """Créer un projet et rendre son créateur administrateur du projet."""
        if self.by_key(key) is not None:
            raise AlreadyExistsError("clé de projet déjà utilisée")
        project = Project(key=key, name=name, description=description, created_by=creator.id)
        self.session.add(project)
        self.session.flush()
        self.session.add(Membership(project_id=project.id, user_id=creator.id, role="admin"))
        self.session.flush()
        return project

    def by_key(self, key: str) -> Project | None:
        return self.session.scalar(select(Project).where(Project.key == key))

    def visible(self, key: str, user: User, is_admin: bool = False) -> tuple[Project, Membership | None]:
        """Ne pas révéler l'existence d'un projet étranger."""
        project = self.by_key(key)
        if project is None:
            raise NotFoundError("projet introuvable")
        membership = self.session.get(Membership, (project.id, user.id))
        if membership is None and not is_admin:
            raise NotFoundError("projet introuvable")
        return project, membership

    def list_for_user(self, user: User, is_admin: bool = False) -> list[Project]:
        """Lister seulement les projets visibles, ou tous pour un admin global."""
        if is_admin:
            return list(self.session.scalars(select(Project).order_by(Project.key)))
        return list(self.session.scalars(
            select(Project).join(Membership, Membership.project_id == Project.id)
            .where(Membership.user_id == user.id).order_by(Project.key)
        ))

    def members(self, project: Project) -> list[tuple[str, str]]:
        rows = self.session.execute(
            select(User.subject, Membership.role).join(User, Membership.user_id == User.id)
            .where(Membership.project_id == project.id).order_by(User.subject)
        ).all()
        return [(subject, role) for subject, role in rows]

    def add_member(self, project: Project, subject: str, role: str) -> None:
        """Ajouter ou changer le rôle d'un membre ; réservé aux administrateurs."""
        if role not in VALID_ROLES:
            raise ValueError("rôle inconnu")
        user = UserRepository(self.session).by_subject(subject)
        if user is None:
            raise NotFoundError("utilisateur introuvable")
        membership = self.session.get(Membership, (project.id, user.id))
        if membership is None:
            self.session.add(Membership(project_id=project.id, user_id=user.id, role=role))
        else:
            membership.role = role
        self.session.flush()

    def require_project_role(self, membership: Membership | None, is_admin: bool, *roles: str) -> None:
        """Refuser une action si ni le rôle global ni le rôle projet ne convient."""
        if is_admin or (membership is not None and membership.role in roles):
            return
        raise ForbiddenError("rôle insuffisant sur ce projet")


class ConfigurationRepository:
    """Historique append-only, arbitrage de version et snapshots indépendants."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def latest(self, project: Project) -> ConfigurationVersion | None:
        return self.session.scalar(
            select(ConfigurationVersion).where(ConfigurationVersion.project_id == project.id)
            .order_by(ConfigurationVersion.version.desc())
        )

    def create_version(
        self, project: Project, document: dict[str, Any], digest: str, author: User,
        expected_version: int | None,
    ) -> ConfigurationVersion:
        """Publier une version ; conflit si ``expected_version`` est obsolète."""
        current = self.latest(project)
        actual = current.version if current is not None else 0
        if expected_version is not None and expected_version != actual:
            raise VersionConflict("version attendue obsolète")
        version = ConfigurationVersion(
            project_id=project.id, version=actual + 1, document=document, digest=digest,
            author_id=author.id, parent_id=current.id if current is not None else None,
        )
        self.session.add(version)
        try:
            self.session.flush()
        except IntegrityError as error:
            raise VersionConflict("version déjà publiée") from error
        return version

    def list_versions(self, project: Project) -> list[ConfigurationVersion]:
        return list(self.session.scalars(
            select(ConfigurationVersion).where(ConfigurationVersion.project_id == project.id)
            .order_by(ConfigurationVersion.version)
        ))

    def get_version(self, project: Project, version: int) -> ConfigurationVersion | None:
        return self.session.scalar(
            select(ConfigurationVersion).where(
                ConfigurationVersion.project_id == project.id, ConfigurationVersion.version == version
            )
        )

    def count(self, project: Project) -> int:
        return int(self.session.scalar(
            select(func.count()).select_from(ConfigurationVersion)
            .where(ConfigurationVersion.project_id == project.id)
        ) or 0)
