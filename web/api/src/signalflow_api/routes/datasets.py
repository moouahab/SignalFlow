"""Datasets CSV : multipart borné, historique, aperçu et téléchargement autorisés."""
import csv
import io
import itertools

from fastapi import APIRouter, Depends, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException, MultiPartParser
from starlette.responses import StreamingResponse

from ..datasets.service import DatasetService
from ..dependencies import get_current_user, get_session_factory, require_permission
from ..errors import ApiError

router = APIRouter()


def service(request: Request, factory=Depends(get_session_factory)):
    return DatasetService(factory, request.app.state.settings)


@router.post('/projects/{key}/datasets', status_code=201,
             dependencies=[Depends(require_permission('dataset:write'))],
             openapi_extra={'requestBody': {'required': True, 'content': {
                 'multipart/form-data': {'schema': {'type': 'object',
                     'required': ['file', 'name', 'target'], 'properties': {
                         'file': {'type': 'string', 'format': 'binary'},
                         'name': {'type': 'string', 'maxLength': 64},
                         'target': {'type': 'string', 'maxLength': 128}}}}}}})
async def upload(key: str, request: Request, current=Depends(get_current_user), svc=Depends(service)):
    # Autorisation avant de parser/spooler le multipart.
    with svc.access(current, key, write=True):
        pass
    parser = MultiPartParser(request.headers, request.stream(), max_files=1,
                             max_fields=2, max_part_size=4096)
    try:
        form = await parser.parse()
    except BaseException as error:
        # Starlette closes these only for MultiPartException, not for transport
        # limits, timeout or cancellation. Close every spool on those paths too.
        for spool in parser._files_to_close_on_error:
            spool.close()
        if isinstance(error, MultiPartException):
            raise ApiError(400, 'INVALID_MULTIPART', 'Formulaire multipart invalide.') from None
        raise
    try:
        if (len(form.multi_items()) != 3 or set(form) != {'file', 'name', 'target'}
                or not isinstance(form['file'], UploadFile)
                or not isinstance(form['name'], str) or not isinstance(form['target'], str)):
            raise ApiError(422, 'INVALID_UPLOAD', 'Champs file, name et target requis.')
        return await run_in_threadpool(svc.upload, current, key, form['name'], form['target'], form['file'])
    finally:
        await form.close()


@router.get('/projects/{key}/datasets', dependencies=[Depends(require_permission('dataset:read'))])
def listing(key: str, offset: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100),
            current=Depends(get_current_user), svc=Depends(service)):
    return svc.list(current, key, offset, limit)


@router.get('/projects/{key}/datasets/{identity}', dependencies=[Depends(require_permission('dataset:read'))])
def details(key: str, identity: str, current=Depends(get_current_user), svc=Depends(service)):
    return svc.get(current, key, identity)


@router.get('/projects/{key}/datasets/{identity}/preview', dependencies=[Depends(require_permission('dataset:read'))])
def preview(key: str, identity: str, limit: int = Query(10, ge=1, le=20),
            current=Depends(get_current_user), svc=Depends(service)):
    row = svc.get(current, key, identity)
    with svc.storage().open(row['id']) as stream:
        with io.TextIOWrapper(stream, encoding='utf-8-sig', newline='') as text:
            reader = csv.reader(text)
            next(reader)
            return {'columns': row['columns'], 'rows': list(itertools.islice(reader, limit))}


@router.get('/projects/{key}/datasets/{identity}/download', dependencies=[Depends(require_permission('dataset:read'))])
def download(key: str, identity: str, current=Depends(get_current_user), svc=Depends(service)):
    row = svc.get(current, key, identity)
    stream = svc.storage().open(row['id'])

    def chunks():
        with stream:
            while chunk := stream.read(65536):
                yield chunk

    return StreamingResponse(chunks(), media_type='text/csv', headers={
        'Content-Disposition': f'attachment; filename="{row["id"]}.csv"',
        'Content-Length': str(row['size_bytes'])})
