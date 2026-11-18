"""Validation sans exécution : politique HTTP puis contrat métier canonique."""
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from signalflow.config.loader.yaml_loader import StrictSafeLoader
from signalflow.ml.validation import validate_training_spec

from ..errors import ApiError, safe_path
from ..schemas.models import ErrorDetail, TrainingPayload, YamlExportResponse, YamlImportRequest


def training_schema() -> dict[str, Any]:
    """Retourner le schéma public, avec restrictions HTTP et règles ML embarquées."""
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            **TrainingPayload.model_json_schema()}


def validate_payload(payload: TrainingPayload) -> TrainingPayload:
    """Valider sans charger de dataset ; lever ApiError ou ConfigError si invalide."""
    document = payload.model_dump(exclude_unset=True)
    details = []
    for error in Draft202012Validator(training_schema()).iter_errors(document):
        details.append(ErrorDetail(path=safe_path(list(error.absolute_path)), rule=str(error.validator)))
        if len(details) == 20:
            break
    if details:
        raise ApiError(422, 'TRAINING_VALIDATION_ERROR', 'La configuration HTTP est invalide.', details)
    spec = validate_training_spec(document)
    return TrainingPayload.model_validate(spec.to_dict())


def parse_yaml(text: str) -> dict[str, Any]:
    """Parser un YAML strict sans toucher au système de fichiers."""
    try:
        document = yaml.load(text, Loader=StrictSafeLoader)
    except ApiError:
        raise
    except yaml.YAMLError as error:
        raise ApiError(422, 'YAML_SYNTAX_ERROR', 'Le document YAML est invalide.') from error
    if not isinstance(document, dict):
        raise ApiError(422, 'YAML_ROOT_ERROR', 'La racine du document doit être un objet.')
    return document


def import_yaml(request: YamlImportRequest) -> TrainingPayload:
    """Valider un YAML importé en utilisant exactement le contrat JSON."""
    return validate_payload(TrainingPayload.model_validate(parse_yaml(request.yaml)))


def export_yaml(payload: TrainingPayload) -> YamlExportResponse:
    """Produire un YAML canonique depuis une configuration déjà validée."""
    validated = validate_payload(payload)
    text = yaml.safe_dump(validated.model_dump(exclude_unset=True), sort_keys=False,
                          allow_unicode=True, default_flow_style=False)
    return YamlExportResponse(yaml=text, configuration=validated)
