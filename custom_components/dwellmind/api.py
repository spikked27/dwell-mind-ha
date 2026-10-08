"""Bounded authenticated worker client; never log keys or raw response bodies."""
import asyncio
import json
from urllib.parse import urlsplit

import aiohttp


class WorkerError(Exception):
    """Worker connection or request failure."""


class WorkerAuthError(WorkerError):
    """Pairing key rejected."""


def validate_connection(url, key, allow_http=False):
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (ValueError, TypeError):
        raise WorkerError('Invalid worker origin') from None
    if (parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {'','/'} or parsed.query or parsed.fragment
            or parsed.scheme == 'http' and not allow_http or port is not None and not 1 <= port <= 65535):
        raise WorkerError('Invalid worker origin or plaintext consent')
    if not isinstance(key, str) or not 32 <= len(key) <= 128 or not key.isascii() or any(ord(c) < 33 for c in key):
        raise WorkerAuthError('Invalid pairing key')
    return url.rstrip('/')


class WorkerClient:
    def __init__(self, session, url, key, allow_http=False):
        self.session = session
        self.url = validate_connection(url, key, allow_http)
        self.key = key

    async def request(self, method, path, payload=None):
        if path not in {'/v1/status','/v1/config','/v1/start','/v1/stop','/v1/events'}:
            raise WorkerError('Unsupported worker request')
        try:
            async with asyncio.timeout(8):
                async with self.session.request(method, self.url+path, json=payload,
                                                headers={'Authorization':'Bearer '+self.key},
                                                allow_redirects=False) as response:
                    if response.status == 401:
                        raise WorkerAuthError('Pairing key rejected')
                    if response.status != 200:
                        raise WorkerError('Worker request refused')
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(4096):
                        raw.extend(chunk)
                        if len(raw) > 131072:
                            raise WorkerError('Worker response exceeds limit')
                    result = json.loads(raw)
                    if not isinstance(result, dict) or result.get('protocol') != 1 or result.get('control_enabled') is not False:
                        raise WorkerError('Unsupported worker protocol')
                    return result
        except (aiohttp.ClientError, TimeoutError, ValueError, TypeError):
            raise WorkerError('Cannot communicate with worker') from None
