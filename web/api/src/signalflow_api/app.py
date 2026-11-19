"""Fabrique FastAPI indépendante du moteur d'exécution."""
from fastapi import FastAPI
from sqlalchemy.orm import sessionmaker

from .auth.identity import DatabaseTokenResolver
from .errors import install_handlers
from .middleware import RequestBoundary
from .routes.configurations import router as configurations_router
from .routes.projects import router as projects_router
from .routes.datasets import router as datasets_router
from .routes.runs import router as runs_router
from .routes.training import router as training_router
from .routes.experiments import router as experiments_router
from .routes.models import router as models_router
from .schemas.models import ErrorResponse
from .settings import Settings


def _database(resolved: Settings):
    """Construire la session et la résolution de jetons depuis le DSN opérateur."""
    if not resolved.database_url:
        return None, None
    from signalflow.persistence.repositories import TokenRepository
    from signalflow.persistence.session import create_database_engine, init_database, session_scope

    engine = create_database_engine(resolved.database_url)
    if resolved.database_url.startswith("sqlite"):
        init_database(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def lookup(digest: str):
        with session_scope(factory) as session:
            return TokenRepository(session).find(digest)

    return factory, lookup


def create_app(settings: Settings | None = None) -> FastAPI:
    """Créer une application locale ; les paramètres sont fournis par l'opérateur."""
    resolved = settings or Settings.from_env()
    session_factory, token_lookup = _database(resolved)
    resolver = resolved.token_resolver
    if resolver is None and token_lookup is not None:
        resolver = DatabaseTokenResolver(token_lookup)
    app = FastAPI(
        title="SignalFlow Local API", version="0.1.0", redoc_url=None,
        description="API locale authentifiée : validation, projets et versions de configuration.",
        responses={status: {"model": ErrorResponse} for status in
                   (400, 401, 403, 408, 409, 413, 415, 422, 500, 501, 503)},
    )
    app.state.settings = resolved
    app.state.token_resolver = resolver
    app.state.database_url = resolved.database_url
    app.state.session_factory = session_factory
    install_handlers(app)
    for router in (training_router, projects_router, configurations_router, datasets_router, runs_router,
                   experiments_router, models_router):
        app.include_router(router, prefix="/api/v1")
    app.add_middleware(RequestBoundary, settings=resolved)
    return app
