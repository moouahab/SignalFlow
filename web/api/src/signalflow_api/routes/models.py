from fastapi import APIRouter, Depends, Request
from ..dependencies import get_current_user, get_session_factory, require_permission
from ..models.service import ModelService, MoveAlias, Name, PromoteModel

router = APIRouter()


def service(request: Request, factory=Depends(get_session_factory)):
    return ModelService(factory, request.app.state.settings)


@router.post('/projects/{key}/models', status_code=201, dependencies=[Depends(require_permission('model:write'))])
def promote(key: str, payload: PromoteModel, current=Depends(get_current_user), svc=Depends(service)):
    return svc.promote(current, key, payload)


@router.get('/projects/{key}/models', dependencies=[Depends(require_permission('model:read'))])
def listing(key: str, current=Depends(get_current_user), svc=Depends(service)):
    return svc.list_models(current, key)


@router.get('/projects/{key}/models/{name}/versions', dependencies=[Depends(require_permission('model:read'))])
def versions(key: str, name: Name, current=Depends(get_current_user), svc=Depends(service)):
    return svc.versions(current, key, name)


@router.get('/projects/{key}/models/{name}/aliases/{alias}', dependencies=[Depends(require_permission('model:read'))])
def alias(key: str, name: Name, alias: Name, current=Depends(get_current_user), svc=Depends(service)):
    return svc.get_alias(current, key, name, alias)


@router.put('/projects/{key}/models/{name}/aliases/{alias}', dependencies=[Depends(require_permission('model:write'))])
def move_alias(key: str, name: Name, alias: Name, payload: MoveAlias,
               current=Depends(get_current_user), svc=Depends(service)):
    return svc.move_alias(current, key, name, alias, payload)


@router.get('/projects/{key}/models/{name}/audit', dependencies=[Depends(require_permission('model:read'))])
def audit(key: str, name: Name, current=Depends(get_current_user), svc=Depends(service)):
    return svc.audit(current, key, name)
