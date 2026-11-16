"""Validation sans exécution : politique HTTP puis contrat métier canonique."""
from typing import Any

from jsonschema import Draft202012Validator
from signalflow.ml.validation import validate_training_spec

from ..errors import ApiError, safe_path
from ..schemas.models import ErrorDetail, TrainingPayload


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
