"""Routes versions : historique append-only, comparaison et export YAML."""
from fastapi import APIRouter, Depends, Query, Response, status

from ..application.configurations import ConfigurationService
from ..dependencies import CurrentUser, get_current_user, get_session_factory, require_permission
from ..schemas.projects import (
    ConfigurationCreate, ConfigurationDiffResponse, ConfigurationListResponse,
    ConfigurationVersionResponse,
)

router = APIRouter()


def _service(factory=Depends(get_session_factory)) -> ConfigurationService:
    return ConfigurationService(factory)


@router.post('/projects/{key}/configurations', status_code=status.HTTP_201_CREATED,
             response_model=ConfigurationVersionResponse,
             dependencies=[Depends(require_permission('config:write'))])
def create_version(key: str, payload: ConfigurationCreate,
                   current: CurrentUser = Depends(get_current_user),
                   service: ConfigurationService = Depends(_service)) -> ConfigurationVersionResponse:
    """Publier une versionValidée ; la concurrence est arbitrée par un conflit 409."""
    return service.create(current, key, payload)


@router.get('/projects/{key}/configurations', response_model=ConfigurationListResponse,
            dependencies=[Depends(require_permission('config:read'))])
def list_versions(key: str, current: CurrentUser = Depends(get_current_user),
                  service: ConfigurationService = Depends(_service)) -> ConfigurationListResponse:
    """Lire l'historique complet d'un projet visible."""
    return service.list(current, key)


@router.get('/projects/{key}/configurations/compare', response_model=ConfigurationDiffResponse,
            dependencies=[Depends(require_permission('config:read'))])
def compare_versions(key: str, source: int = Query(ge=1), target: int = Query(ge=1),
                     current: CurrentUser = Depends(get_current_user),
                     service: ConfigurationService = Depends(_service)) -> ConfigurationDiffResponse:
    """Comparer deux versions sans modifier l'historique."""
    return service.compare(current, key, source, target)


@router.get('/projects/{key}/configurations/{version}', response_model=ConfigurationVersionResponse,
            dependencies=[Depends(require_permission('config:read'))])
def get_version(key: str, version: int, current: CurrentUser = Depends(get_current_user),
                service: ConfigurationService = Depends(_service)) -> ConfigurationVersionResponse:
    """Lire une version immuable et son snapshot indépendant."""
    return service.get(current, key, version)


@router.get('/projects/{key}/configurations/{version}/yaml',
            dependencies=[Depends(require_permission('config:read'))])
def export_version_yaml(key: str, version: int, current: CurrentUser = Depends(get_current_user),
                        service: ConfigurationService = Depends(_service)) -> Response:
    """Exporter une version en YAML produit par le serveur."""
    return Response(content=service.export(current, key, version), media_type='application/x-yaml')
