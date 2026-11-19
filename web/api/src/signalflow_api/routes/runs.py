"""API des jobs durables et journal SSE reprenable par Last-Event-ID."""
import asyncio
import json
import time

from fastapi import APIRouter, Depends, Header, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import StreamingResponse
from ..dependencies import get_current_user, get_principal, get_session_factory, require_permission
from ..errors import ApiError
from ..jobs.service import JobService, SubmitJob
from ..jobs.states import TERMINAL

router = APIRouter()


def service(request: Request, factory=Depends(get_session_factory)):
    return JobService(factory, request.app.state.settings)


@router.post('/projects/{key}/runs', status_code=202,
             dependencies=[Depends(require_permission('training:create'))])
def submit(key: str, payload: SubmitJob,
           idempotency_key: str = Header(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9_.:-]+$'),
           current=Depends(get_current_user), svc=Depends(service)):
    return svc.submit(current, key, payload, idempotency_key)


@router.get('/projects/{key}/runs', dependencies=[Depends(require_permission('training:read'))])
def listing(key: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100),
            current=Depends(get_current_user), svc=Depends(service)):
    return svc.list(current, key, offset, limit)


@router.get('/projects/{key}/runs/{identity}', dependencies=[Depends(require_permission('training:read'))])
def details(key: str, identity: str, current=Depends(get_current_user), svc=Depends(service)):
    return svc.get(current, key, identity)


@router.post('/projects/{key}/runs/{identity}/cancel', dependencies=[Depends(require_permission('training:create'))])
def cancel(key: str, identity: str, current=Depends(get_current_user), svc=Depends(service)):
    return svc.cancel(current, key, identity)


@router.get('/projects/{key}/runs/{identity}/events', dependencies=[Depends(require_permission('training:read'))])
async def events(key: str, identity: str, request: Request,
                 last_event_id: int = Header(0, ge=0), current=Depends(get_current_user), svc=Depends(service)):
    await run_in_threadpool(svc.get, current, key, identity)

    async def stream():
        cursor = last_event_id
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline and not await request.is_disconnected():
            try:
                # Recontrôler appartenance et identité à chaque lecture.
                fresh = await run_in_threadpool(get_current_user,
                    await run_in_threadpool(get_principal, request),
                    request.app.state.session_factory)
                rows = await run_in_threadpool(svc.events, fresh, key, identity, cursor)
                job = await run_in_threadpool(svc.get, fresh, key, identity)
            except ApiError:
                return
            for row in rows:
                cursor = row['id']
                yield f'id: {cursor}\nevent: {row["event"]}\ndata: {json.dumps(row)}\n\n'
            if job['state'] in TERMINAL:
                return
            yield ': heartbeat\n\n'
            await asyncio.sleep(.5)

    return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})
