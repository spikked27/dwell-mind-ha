"""Stateless MCP 2025-03-26 JSON HTTP transport; no SSE or sessions."""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading

from policy import MAX_OUTPUT, SafeError, strict_json

PROTOCOL = "2025-03-26"
MAX_INPUT = 16384


def tool_list(config):
    entity = {"type": "string", "enum": list(config.entities)}
    common = {"entity_id": entity, "retention_policy": {"type": "string", "enum": config.policies},
              "start": {"type": "string", "description": "Inclusive RFC3339 timestamp"},
              "end": {"type": "string", "description": "Exclusive RFC3339 timestamp"},
              "limit": {"type": "integer", "minimum": 1, "maximum": config.max_rows}}
    specs = [
        ("catalog", "Configured allowlist; not verified archive schema.", {}, []),
        ("retention_policies", "Approved fixed-database retention metadata.", {}, []),
        ("fields", "Approved measurement fields, not entity-specific coverage.", {"entity_id": entity}, ["entity_id"]),
        ("sample", "Bounded raw selected-field rows; no arbitrary query interface.",
         {**common, "fields": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 12},
          "order": {"type": "string", "enum": ["oldest", "newest"]}}, ["entity_id", "retention_policy", "start", "end"]),
        ("coverage_window", "Coverage of returned rows, not global archive boundaries or counts.",
         {**common, "field": {"type": "string"}}, ["entity_id", "retention_policy", "start", "end"]),
    ]
    return [{"name": n, "description": d,
             "inputSchema": {"type": "object", "properties": p, "required": r, "additionalProperties": False},
             "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True}}
            for n, d, p, r in specs]


class MCP:
    def __init__(self, policy):
        self.policy = policy

    def dispatch(self, message):
        if isinstance(message, list):
            if not 1 <= len(message) <= 4 or any(isinstance(m, list) for m in message):
                raise SafeError("Invalid batch.")
            replies = [self.dispatch(m) for m in message]
            return [r for r in replies if r is not None] or None
        if (not isinstance(message, dict) or message.get("jsonrpc") != "2.0"
                or not isinstance(message.get("method"), str)
                or message.keys() - {"jsonrpc", "id", "method", "params"}):
            raise SafeError("Invalid JSON-RPC request.")
        params = message.get("params", {})
        if not isinstance(params, dict):
            raise SafeError("Invalid params.")
        if "id" not in message:
            if message["method"] not in {"notifications/initialized", "notifications/cancelled"}:
                raise SafeError("Unsupported notification.")
            return None
        request_id = message["id"]
        if (type(request_id) not in {str, int} or len(str(request_id)) > 128):
            raise SafeError("Invalid request ID.")
        try:
            method = message["method"]
            if method == "initialize":
                result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {"listChanged": False}},
                          "serverInfo": {"name": "influx-readonly", "version": "0.1.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                if params.keys() - {"_meta"}:
                    raise SafeError("Unsupported list argument.")
                result = {"tools": tool_list(self.policy.config)}
            elif method == "tools/call":
                if params.keys() - {"name", "arguments", "_meta"}:
                    raise SafeError("Unsupported call argument.")
                try:
                    data = self.policy.call(params.get("name"), params.get("arguments", {}))
                    text = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                    if len(text.encode()) > MAX_OUTPUT // 8:
                        raise SafeError("Result byte limit exceeded; request fewer rows or fields.")
                    result = {"content": [{"type": "text", "text": text}], "isError": False}
                except SafeError as exc:
                    result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
            else:
                return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Unsupported method."}}
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        except SafeError as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(exc)}}
        except Exception:
            return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32603, "message": "Internal connector error."}}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, address, mcp, token, hosts):
        self.mcp, self.hosts = mcp, frozenset(hosts)
        self.token_digest = hashlib.sha256(token.encode()).digest()
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(address, Handler)

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(10)
        return sock, address

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass  # Never log untrusted messages, credentials, or tracebacks.


class Handler(BaseHTTPRequestHandler):
    server_version, sys_version = "InfluxReadOnly", ""

    def log_message(self, format, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        # BaseHTTPRequestHandler otherwise reflects unsupported methods in HTML.
        self._reply(code, {"error": "HTTP request refused."})

    def _reply(self, status, body=None):
        payload = b"" if body is None else json.dumps(body, allow_nan=False, separators=(",", ":")).encode()
        if len(payload) > MAX_OUTPUT:
            status, payload = 413, b'{"error":"Response byte limit exceeded."}'
        self.send_response(status)
        for key, value in {"Content-Type": "application/json", "Content-Length": str(len(payload)),
                           "Cache-Control": "no-store", "Connection": "close", "X-Content-Type-Options": "nosniff"}.items():
            self.send_header(key, value)
        if status == 405:
            self.send_header("Allow", "POST")
        self.end_headers()
        self.close_connection = True
        if payload:
            self.wfile.write(payload)

    def _authorized(self):
        if (self.headers.get("Origin") is not None or len(self.headers.get_all("Host", [])) != 1
                or self.headers.get("Host") not in self.server.hosts):
            self._reply(403, {"error": "Origin or Host refused."})
            return False
        auth = self.headers.get_all("Authorization", [])
        if (len(auth) != 1 or not auth[0].startswith("Bearer ") or len(auth[0]) > 300
                or not hmac.compare_digest(hashlib.sha256(auth[0][7:].encode()).digest(), self.server.token_digest)):
            self._reply(401, {"error": "Authentication required."})
            return False
        if self.path != "/mcp":
            self._reply(404, {"error": "Not found."})
            return False
        versions = self.headers.get_all("MCP-Protocol-Version", [])
        if len(versions) > 1 or (versions and versions[0] != PROTOCOL):
            self._reply(400, {"error": "Unsupported protocol."})
            return False
        return True

    def do_GET(self):
        if self._authorized():
            self._reply(405, {"error": "No SSE stream."})

    def do_DELETE(self):
        if self._authorized():
            self._reply(405, {"error": "Stateless server."})

    def do_POST(self):
        if not self._authorized():
            return
        if (self.headers.get("Transfer-Encoding") is not None
                or len(self.headers.get_all("Content-Length", [])) != 1
                or len(self.headers.get_all("Content-Type", [])) != 1):
            self._reply(400, {"error": "Invalid request framing."})
            return
        if self.headers.get_content_type() != "application/json" or self.headers.get("Content-Encoding") is not None:
            self._reply(415, {"error": "Uncompressed JSON required."})
            return
        try:
            length_text = self.headers["Content-Length"]
            if not length_text.isascii() or not length_text.isdigit() or not 1 <= int(length_text) <= MAX_INPUT:
                raise SafeError("Invalid length.")
            length = int(length_text)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise SafeError("Incomplete input.")
            result = self.server.mcp.dispatch(strict_json(raw))
            self._reply(202 if result is None else 200, result)
        except (SafeError, ValueError, OSError, RecursionError):
            self._reply(400, {"error": "Invalid or oversized request."})
