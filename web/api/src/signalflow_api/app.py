"""Fabrique FastAPI indépendante du moteur d'exécution."""
from fastapi import FastAPI
from .errors import install_handlers
from .middleware import RequestBoundary
from .routes.training import router
from .schemas.models import ErrorResponse
from .settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Créer une application locale ; les paramètres sont fournis par l'opérateur."""
    app = FastAPI(title='SignalFlow Local API', version='0.1.0', redoc_url=None,
                  description="Validation locale uniquement. Authentification et jobs durables non implémentés.",
                  responses={status: {'model': ErrorResponse} for status in
                             (400, 401, 403, 408, 413, 415, 422, 500, 501, 503)})
    app.state.settings = settings or Settings()
    install_handlers(app)
    app.include_router(router, prefix='/api/v1')
    app.add_middleware(RequestBoundary, settings=app.state.settings)
    return app
