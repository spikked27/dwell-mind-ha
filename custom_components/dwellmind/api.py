"""Bounded authenticated worker client; never log keys or raw response bodies."""
import asyncio
import json
from urllib.parse import urlsplit

import aiohttp


class WorkerError(Exception):
    """Worker connection or request failure."""

    def __init__(self, message, code='cannot_connect'):
        super().__init__(message)
        self.code = code


class WorkerAuthError(WorkerError):
    """Pairing key rejected."""


def validate_connection(url, key, allow_http=False):
    if isinstance(url, str):
        url = url.strip()
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except (ValueError, TypeError):
        raise WorkerError('Invalid worker origin','invalid_url') from None
    if (parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password
            or parsed.path not in {'','/'} or parsed.query or parsed.fragment
            or port is not None and not 1 <= port <= 65535):
        raise WorkerError('Invalid worker origin','invalid_url')
    if parsed.scheme == 'http' and not allow_http:
        raise WorkerError('Plaintext consent required','http_not_allowed')
    if isinstance(key, str):
        key = key.strip()
    if not isinstance(key, str) or not 32 <= len(key) <= 128 or not key.isascii() or any(ord(c) < 33 for c in key):
        raise WorkerAuthError('Invalid pairing key')
    return url.rstrip('/')


class WorkerClient:
    def __init__(self, session, url, key, allow_http=False):
        self.session = session
        self.url = validate_connection(url, key, allow_http)
        self.key = key.strip()

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
                        raise WorkerError('Worker request refused','worker_http_error')
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(4096):
                        raw.extend(chunk)
                        if len(raw) > 131072:
                            raise WorkerError('Worker response exceeds limit','invalid_response')
                    result = json.loads(raw)
                    if not isinstance(result, dict) or result.get('protocol') != 1 or result.get('control_enabled') is not False:
                        raise WorkerError('Unsupported worker protocol','incompatible_worker')
                    return result
        except aiohttp.ClientSSLError:
            raise WorkerError('Worker TLS verification failed','tls_error') from None
        except TimeoutError:
            raise WorkerError('Worker request timed out','timeout') from None
        except aiohttp.ClientError:
            raise WorkerError('HA cannot open worker connection','network_error') from None
        except (ValueError, TypeError):
            raise WorkerError('Worker returned invalid JSON','invalid_response') from None
