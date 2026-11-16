"""Traduction centralisée des erreurs : aucune valeur, trace ou chemin interne exposé."""
import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from signalflow.config.loader.errors import ConfigError
from .schemas.catalog import component_schema
from .schemas.models import ErrorDetail, ErrorResponse


def _field_names(value: Any) -> set[str]:
    if isinstance(value, dict):
        result = set(value.get('properties', {}))
        for child in value.values():
            result.update(_field_names(child))
        return result
    if isinstance(value, list):
        return set().union(*(_field_names(child) for child in value))
    return set()


SAFE_FIELDS = {'training', 'dataset', 'tracking', 'monitoring'} | set().union(
    *(_field_names(component_schema(name)) for name in ('training','dataset','tracking','monitoring'))
)


def safe_path(parts: list[Any]) -> str:
    """Conserver uniquement les noms de champs du contrat, sans refléter une clé arbitraire."""
    return '$' + ''.join('.' + (part if isinstance(part, str) and part in SAFE_FIELDS else '*')
                         for part in parts if part != 'body')


class ApiError(Exception):
    """Erreur HTTP contrôlée avec texte interne fixe et détails déjà expurgés."""

    def __init__(self, status: int, code: str, message: str,
                 details: list[ErrorDetail] | None = None) -> None:
        self.status, self.code, self.message = status, code, message
        self.details = details or []
        super().__init__(code)


def error_response(request_id: str, status: int, code: str, message: str,
                   details: list[ErrorDetail] | None = None) -> JSONResponse:
    """Construire la même enveloppe pour middleware, dépendances et routes."""
    document = ErrorResponse(code=code, message=message, details=details or [], request_id=request_id)
    return JSONResponse(document.model_dump(), status_code=status,
                        headers={'X-Request-ID': request_id, 'Cache-Control': 'no-store'})


def install_handlers(app: FastAPI) -> None:
    """Installer les traductions HTTP ; les messages des exceptions ne sont jamais renvoyés."""
    @app.exception_handler(ApiError)
    async def controlled(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(request.state.request_id, exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def request_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [ErrorDetail(path=safe_path(list(item['loc'])), rule=item['type'])
                   for item in exc.errors()[:20]]
        return error_response(request.state.request_id, 422, 'REQUEST_VALIDATION_ERROR',
                              'La requête ne respecte pas le contrat.', details)

    @app.exception_handler(ConfigError)
    async def engine_validation(request: Request, exc: ConfigError) -> JSONResponse:
        # The engine's formatted message may include rejected data and local filenames.
        details = [ErrorDetail(path=safe_path(re.findall(r'[A-Za-z_][A-Za-z_0-9]*', issue.json_path)),
                               rule=issue.validator or 'semantic') for issue in exc.issues[:20]]
        return error_response(request.state.request_id, 422, 'TRAINING_VALIDATION_ERROR',
                              'La configuration ML est invalide.', details)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return error_response(request.state.request_id, exc.status_code, 'HTTP_ERROR',
                              'La requête HTTP ne peut pas être traitée.')
