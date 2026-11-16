"""Frontière ASGI bornée avant parsing FastAPI, sans dépendre du Content-Length annoncé."""
import asyncio
import json
import logging
import re
import math
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import ApiError, error_response
from .settings import Settings

logger = logging.getLogger('signalflow.api')


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def _nonfinite(value: str) -> None:
    raise ValueError('non-finite JSON')


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError('non-finite JSON')
    return number


class RequestBoundary:
    """Limiter taille/temps/profondeur et traduire les erreurs inattendues sans traceback HTTP."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app, self.settings = app, settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Traiter les requêtes HTTP ; laisser le cycle de vie ASGI inchangé."""
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        request_id = uuid4().hex
        scope.setdefault('state', {})['request_id'] = request_id
        started = False

        async def secured_send(message: Message) -> None:
            nonlocal started
            if message['type'] == 'http.response.start':
                started = True
                message['headers'] = [*(h for h in message.get('headers', [])
                    if h[0].lower() not in {b'x-request-id', b'cache-control', b'x-content-type-options'}),
                    (b'x-request-id', request_id.encode()), (b'x-content-type-options', b'nosniff'),
                    (b'cache-control', b'no-store')]
            await send(message)

        try:
            headers = self._check_headers(scope)
            body = await self._read_body(receive)
            if scope['method'] in ('POST','PUT','PATCH'):
                media_type = headers.get(b'content-type', b'').split(b';')[0].strip().lower()
                if media_type != b'application/json':
                    raise ApiError(415,'UNSUPPORTED_MEDIA_TYPE','Un document application/json est requis.')
                self._check_json(body)
            sent = False

            async def replay() -> Message:
                nonlocal sent
                if not sent:
                    sent = True
                    return {'type':'http.request', 'body':body, 'more_body':False}
                return await receive()

            await self.app(scope, replay, secured_send)
        except ApiError as exc:
            if started:
                raise
            await error_response(request_id,exc.status,exc.code,exc.message,exc.details)(scope,receive,secured_send)
        except Exception:
            logger.error('request_failed request_id=%s', request_id)
            if started:
                raise
            await error_response(request_id,500,'INTERNAL_ERROR','Une erreur interne est survenue.')(scope,receive,secured_send)

    def _check_headers(self, scope: Scope) -> dict[bytes, bytes]:
        headers = dict(scope['headers'])
        hosts = [v for k,v in scope['headers'] if k == b'host']
        try:
            host = hosts[0].decode('ascii')
            match = re.fullmatch(r'(localhost|127\.0\.0\.1|\[::1\])(?::([0-9]{1,5}))?', host)
            valid = len(hosts) == 1 and match is not None
            if valid and match.group(2):
                valid = 1 <= int(match.group(2)) <= 65535
        except (IndexError, UnicodeError, ValueError):
            valid = False
        if not valid:
            raise ApiError(400,'INVALID_HOST','Hôte HTTP non autorisé.')
        origin = headers.get(b'origin')
        if origin is not None and origin != f"{scope.get('scheme','http')}://{host}".encode():
            raise ApiError(403,'ORIGIN_NOT_ALLOWED','Origine non autorisée.')
        if headers.get(b'content-encoding',b'identity').lower() != b'identity':
            raise ApiError(415,'UNSUPPORTED_ENCODING','Les corps compressés ne sont pas acceptés.')
        lengths = [v for k,v in scope['headers'] if k == b'content-length']
        if lengths:
            if len(lengths) != 1 or not lengths[0].isdigit():
                raise ApiError(400,'INVALID_CONTENT_LENGTH','Taille de requête invalide.')
            if len(lengths[0]) > 12 or int(lengths[0]) > self.settings.max_request_bytes:
                raise ApiError(413,'REQUEST_TOO_LARGE','La requête dépasse la taille autorisée.')
        return headers

    async def _read_body(self, receive: Receive) -> bytes:
        chunks, size = [], 0
        try:
            async with asyncio.timeout(self.settings.body_timeout_seconds):
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        raise ApiError(400,'REQUEST_DISCONNECTED','Requête interrompue.')
                    chunk = message.get('body',b'')
                    size += len(chunk)
                    if size > self.settings.max_request_bytes:
                        raise ApiError(413,'REQUEST_TOO_LARGE','La requête dépasse la taille autorisée.')
                    chunks.append(chunk)
                    if not message.get('more_body',False):
                        return b''.join(chunks)
        except TimeoutError:
            raise ApiError(408,'REQUEST_TIMEOUT','Délai de lecture dépassé.') from None

    def _check_json(self, body: bytes) -> None:
        try:
            data = json.loads(body.decode('utf-8'), object_pairs_hook=_unique_object, parse_constant=_nonfinite, parse_float=_finite_float)
            pending = [(data, 0)]
            while pending:
                node, depth = pending.pop()
                if depth > self.settings.max_json_depth:
                    raise ValueError('JSON depth exceeded')
                if isinstance(node, dict):
                    pending.extend((v, depth+1) for v in node.values())
                elif isinstance(node, list):
                    pending.extend((v, depth+1) for v in node)
        except (ValueError, UnicodeError, RecursionError):
            raise ApiError(400,'INVALID_JSON','Document JSON UTF-8 invalide, ambigu ou trop imbriqué.') from None
