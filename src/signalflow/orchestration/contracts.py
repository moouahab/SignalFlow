"""Références privées aux artefacts échangés entre étapes sur un hôte partagé."""
import json
from pathlib import Path

import numpy as np
from signalflow.ml.adapters.data import Dataset, Split


def write_json(path, value):
    Path(path).write_text(json.dumps(value, allow_nan=False), encoding='utf-8')


def read_dataset(directory):
    with np.load(Path(directory) / 'dataset.npz', allow_pickle=False) as data:
        return Dataset(data['X'], data['y'], tuple(data['features'].tolist()), str(data['sha256']))


def read_split(directory):
    with np.load(Path(directory) / 'split.npz', allow_pickle=False) as data:
        return Split(data['train'], data['validation'], data['test'])


def request(directory):
    return json.loads((Path(directory) / 'request.json').read_text())
