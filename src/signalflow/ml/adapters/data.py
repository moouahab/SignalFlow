"""Données tabulaires numériques et découpage avant tout apprentissage."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from sklearn import datasets
from sklearn.model_selection import train_test_split

from signalflow.config.loader.files import read_text
from signalflow.ml.errors import DatasetError

BUILTINS = {
    "iris": datasets.load_iris,
    "wine": datasets.load_wine,
    "breast_cancer": datasets.load_breast_cancer,
}


@dataclass(frozen=True)
class Dataset:
    """Valeurs brutes, noms des colonnes et empreinte du contenu effectivement lu."""

    X: NDArray[np.float64]
    y: NDArray[Any]
    features: tuple[str, ...]
    sha256: str


@dataclass(frozen=True)
class Split:
    """Indices disjoints dans le dataset original, persistés pour audit."""

    train: NDArray[np.int64]
    validation: NDArray[np.int64]
    test: NDArray[np.int64]

    def as_dict(self) -> dict[str, list[int]]:
        """Convertir les indices en listes JSON.

        Sans paramètres. Retourne train/validation/test ; aucune exception attendue.
        Aucun effet de bord ni modification des tableaux originaux.
        """
        return {name: getattr(self, name).tolist() for name in ("train", "validation", "test")}


def _builtin(source: dict[str, Any], path: Path | None) -> tuple[Any, Any, tuple[str, ...]]:
    bunch = BUILTINS[source["name"]]()
    return bunch.data, bunch.target, tuple(bunch.feature_names)


def _csv(source: dict[str, Any], path: Path | None) -> tuple[Any, Any, tuple[str, ...]]:
    if path is None:
        raise DatasetError("chemin CSV validé manquant")
    # Lecture bornée, pas d'URL ni de moteur de parsing piloté par YAML.
    text = read_text(path, max_bytes=32 * 1024 * 1024)
    try:
        reader = csv.reader(io.StringIO(text), strict=True)
        header = next(reader)
        if len(header) != len(set(header)) or any(not name.strip() for name in header):
            raise DatasetError("colonnes CSV vides ou dupliquées")
        if source["target"] not in header:
            raise DatasetError("colonne cible absente du CSV")
        target = header.index(source["target"])
        features = tuple(name for index, name in enumerate(header) if index != target)
        X, y = [], []
        for row in reader:
            if len(row) != len(header) or not row[target].strip():
                raise DatasetError("ligne CSV incomplète ou cible vide")
            X.append([float(value) if value.strip() else np.nan
                      for index, value in enumerate(row) if index != target])
            y.append(row[target])
        return X, y, features
    except (csv.Error, ValueError, StopIteration):
        raise DatasetError("CSV malformé ou caractéristiques non numériques") from None


def _synthetic(source: dict[str, Any], path: Path | None) -> tuple[Any, Any, tuple[str, ...]]:
    parameters = {key: value for key, value in source.items() if key != "type"}
    X, y = datasets.make_classification(**parameters, n_clusters_per_class=1, flip_y=0.01)
    return X, y, tuple(f"feature_{index}" for index in range(X.shape[1]))


SOURCES = {"builtin": _builtin, "csv": _csv, "synthetic": _synthetic}


def load_dataset(source: dict[str, Any], path: Path | None = None) -> Dataset:
    """Charger un dataset intégré ou un CSV local dont le chemin est déjà validé.

    Args:
        source: déclaration validée par dataset.schema.json.
        path: chemin confiné résolu par load_run_configuration, pour un CSV.
    Returns:
        Dataset numérique ; les cellules de caractéristiques vides sont des NaN.
    Raises:
        DatasetError: données malformées, infinies, vides ou moins de deux classes.
        ConfigError: CSV inaccessible, trop volumineux ou mal encodé.
    Side effects:
        Lit uniquement le dataset demandé ; aucune transformation apprise.
    """
    X, y, features = SOURCES[source["type"]](source, path)
    X, y = np.asarray(X, dtype=np.float64), np.asarray(y)
    if X.ndim != 2 or X.shape[0] < 3 or X.shape[1] == 0:
        raise DatasetError("au moins trois lignes et une caractéristique sont nécessaires")
    if np.isinf(X).any() or np.isnan(X).all(axis=0).any():
        raise DatasetError("caractéristique infinie ou entièrement manquante")
    if len(np.unique(y)) < 2:
        raise DatasetError("au moins deux classes sont nécessaires")
    digest = hashlib.sha256()
    digest.update(memoryview(np.ascontiguousarray(X, dtype='<f8')).cast('B'))
    digest.update(json.dumps([y.tolist(), features], ensure_ascii=True).encode())
    return Dataset(X, y, features, digest.hexdigest())


def split_dataset(dataset: Dataset, settings: dict[str, Any], seed: int) -> Split:
    """Séparer les indices en train, validation et test sans transformation.

    Args:
        dataset: données brutes validées.
        settings: fractions validation/test du total et booléen stratify.
        seed: seed commune et explicite aux deux séparations.
    Returns:
        Split de trois partitions disjointes et exhaustives.
    Raises:
        DatasetError: effectifs insuffisants ou classes absentes du train.
    Side effects:
        Aucun ; ne modifie ni les données ni le générateur aléatoire global.
    """
    indices = np.arange(len(dataset.y))
    stratify = dataset.y if settings["stratify"] else None
    try:
        remaining, test = train_test_split(
            indices, test_size=settings["test"], random_state=seed, stratify=stratify,
        )
        train, validation = train_test_split(
            remaining, test_size=settings["validation"] / (1 - settings["test"]),
            random_state=seed,
            stratify=dataset.y[remaining] if settings["stratify"] else None,
        )
    except ValueError:
        raise DatasetError("effectifs insuffisants pour les fractions et la stratification demandées") from None
    if set(dataset.y[train]) != set(dataset.y):
        raise DatasetError("toutes les classes doivent être représentées dans le train")
    return Split(train, validation, test)
