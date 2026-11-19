from fastapi import APIRouter, Depends, Request
from ..dependencies import get_current_user, get_session_factory, require_permission
from ..experiments.service import CompareRuns, ExperimentService

router = APIRouter()


def service(request: Request, factory=Depends(get_session_factory)):
    return ExperimentService(factory, request.app.state.settings)


@router.post('/projects/{key}/experiments/compare', dependencies=[Depends(require_permission('training:read'))])
def compare(key: str, payload: CompareRuns, current=Depends(get_current_user), svc=Depends(service)):
    return svc.compare(current, key, payload)
