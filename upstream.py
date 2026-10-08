"""Credential-file and HTTPS boundaries. No proxy environment or redirects."""
import base64
import os
import ssl
import stat
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener

from policy import MAX_UPSTREAM, SafeError, strict_json


def read_secret(path):
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
                    or info.st_uid != os.geteuid() or info.st_size > 2048):
                raise SafeError("Secret must be a small, owner-only, service-owned regular file.")
            value = handle.read(2049).decode().rstrip("\r\n")
    except (OSError, UnicodeError):
        raise SafeError("Cannot read service secret.") from None
    if not value or any(ord(c) < 32 for c in value):
        raise SafeError("Invalid service secret.")
    return value


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SafeError("Upstream redirect refused.")


class InfluxClient:
    def __init__(self, config, username, password):
        if ":" in username:
            raise SafeError("Invalid username.")
        self.config = config
        self.authorization = "Basic " + base64.b64encode(f"{username}:{password}".encode()).decode()
        self.opener = build_opener(ProxyHandler({}), NoRedirects(),
                                   HTTPSHandler(context=ssl.create_default_context()))

    def query(self, query):
        request = Request(self.config.url + "/query?" + urlencode({"db": self.config.database, "q": query}),
                          headers={"Authorization": self.authorization, "Accept": "application/json"}, method="GET")
        started = time.monotonic()
        try:
            with self.opener.open(request, timeout=self.config.timeout) as response:
                if response.status != 200 or response.headers.get_content_type() != "application/json":
                    raise SafeError("Unexpected upstream response.")
                if response.headers.get("Content-Encoding", "identity") != "identity":
                    raise SafeError("Compressed upstream response refused.")
                chunks, size = [], 0
                while True:
                    if time.monotonic() - started > self.config.timeout:
                        raise SafeError("Upstream time budget exceeded.")
                    chunk = response.read1(min(65536, MAX_UPSTREAM + 1 - size))
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_UPSTREAM:
                        raise SafeError("Upstream byte limit exceeded.")
                    chunks.append(chunk)
                result = strict_json(b"".join(chunks))
        except HTTPError as exc:
            exc.close()
            raise SafeError("Upstream authentication or HTTP request failed.") from None
        except (URLError, OSError, ValueError):
            raise SafeError("Upstream connection failed.") from None
        if not isinstance(result, dict) or "error" in result:
            raise SafeError("Upstream query rejected.")
        results = result.get("results")
        if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
            raise SafeError("Unexpected upstream result shape.")
        if "error" in results[0] or results[0].get("partial"):
            raise SafeError("Failed or partial upstream result.")
        return results[0]
