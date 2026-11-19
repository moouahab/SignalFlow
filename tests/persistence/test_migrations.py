"""Vérifier les migrations applicatives sur une base SQLite jetable.

Ces tests ne remplacent pas la validation PostgreSQL ; ils évitent les
régressions de schéma Alembic sans toucher à une base utilisateur.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

from sqlalchemy import create_engine, inspect


def test_migrations_upgrade_and_metadata_alignment(tmp_path):
    database = tmp_path / "phase4-test.sqlite"
    root = Path(__file__).resolve().parents[2]
    url = f"sqlite:///{database}"
    env = dict(os.environ, SIGNALFLOW_DATABASE_URL=url)
    for command in ("upgrade", "check"):
        argv = [sys.executable, "-m", "alembic", command]
        if command == "upgrade":
            argv.append("head")
        result = subprocess.run(argv, cwd=root, env=env,
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stdout + result.stderr
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        assert {
            "sf_users", "sf_tokens", "sf_projects", "sf_memberships",
            "sf_configuration_versions",
            "sf_model_registrations", "sf_model_alias_events",
        }.issubset(set(inspector.get_table_names()))
        assert "expires_at" in {column["name"] for column in inspector.get_columns("sf_tokens")}
    finally:
        engine.dispose()
