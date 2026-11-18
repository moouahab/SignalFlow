"""Routes projets : identité obligatoire et visibilité par appartenance."""
from fastapi import APIRouter, Depends, status

from ..application.projects import ProjectService
from ..dependencies import CurrentUser, get_current_user, get_session_factory, require_permission
from ..schemas.projects import MembershipCreate, MembershipResponse, ProjectCreate, ProjectResponse

router = APIRouter()


def _service(factory=Depends(get_session_factory)) -> ProjectService:
    return ProjectService(factory)


@router.post('/projects', status_code=status.HTTP_201_CREATED, response_model=ProjectResponse,
             dependencies=[Depends(require_permission('project:manage'))])
def create_project(payload: ProjectCreate, current: CurrentUser = Depends(get_current_user),
                   service: ProjectService = Depends(_service)) -> ProjectResponse:
    """Créer un projet et enregistrer son créateur comme administrateur du projet."""
    return service.create(current, payload)


@router.get('/projects', response_model=list[ProjectResponse],
            dependencies=[Depends(require_permission('project:read'))])
def list_projects(current: CurrentUser = Depends(get_current_user),
                  service: ProjectService = Depends(_service)) -> list[ProjectResponse]:
    """Lister uniquement les projets visibles pour l'identité authentifiée."""
    return service.list(current)


@router.get('/projects/{key}', response_model=ProjectResponse,
            dependencies=[Depends(require_permission('project:read'))])
def get_project(key: str, current: CurrentUser = Depends(get_current_user),
                service: ProjectService = Depends(_service)) -> ProjectResponse:
    """Lire un projet visible ; un projet étranger reste indiscernable d'un absent."""
    return service.get(current, key)


@router.post('/projects/{key}/members', status_code=status.HTTP_201_CREATED,
             response_model=MembershipResponse,
             dependencies=[Depends(require_permission('project:manage'))])
def add_member(key: str, payload: MembershipCreate, current: CurrentUser = Depends(get_current_user),
               service: ProjectService = Depends(_service)) -> MembershipResponse:
    """Ajouter un membre ou changer son rôle dans un projet."""
    return service.add_member(current, key, payload.subject, payload.role)


@router.get('/projects/{key}/members', response_model=list[MembershipResponse],
            dependencies=[Depends(require_permission('project:read'))])
def list_members(key: str, current: CurrentUser = Depends(get_current_user),
                 service: ProjectService = Depends(_service)) -> list[MembershipResponse]:
    """Lister les membres d'un projet visible."""
    return service.members(current, key)
