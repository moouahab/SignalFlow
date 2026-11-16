"""Projection HTTP des schémas du moteur ; seules les restrictions de transport sont ajoutées."""
from importlib.resources import files
import json
from typing import Any

from signalflow.ml.validation import COMPONENTS


def component_schema(name: str) -> dict[str, Any]:
    """Lire une copie d'un schéma embarqué, sans chemin fourni par le client."""
    filename = COMPONENTS[name]
    document = json.loads(files('signalflow.config').joinpath('schemas', f'{filename}.schema.json').read_text())
    document.pop('$schema', None)
    if name == 'dataset':
        document['properties']['source']['oneOf'] = [
            choice for choice in document['properties']['source']['oneOf']
            if choice['properties']['type']['const'] != 'csv'
        ]
    elif name == 'monitoring':
        document['properties'].pop('directory', None)
        for key in ('prometheus', 'logging'):
            document['properties'][key]['properties']['enabled']['const'] = False
    return document
