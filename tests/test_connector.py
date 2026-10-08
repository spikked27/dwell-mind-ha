"""Dependency-free mocked tests. No sockets, archive access or real credentials."""
import copy
from email.message import Message
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
from urllib.error import HTTPError

from mcp_server import Handler, MCP, MAX_INPUT, tool_list
from policy import Config, Policy, SafeError, instant, rows, strict_json
from upstream import InfluxClient, NoRedirects, read_secret

ROOT = Path(__file__).resolve().parents[1]


def config(**updates):
    data = json.loads((ROOT / "config.example.json").read_text())
    data.update(updates)
    return Config(data)


def arguments(**updates):
    data = {"entity_id": "light.office_ceiling", "retention_policy": "autogen",
            "start": "2024-10-01T00:00:00Z", "end": "2024-10-01T01:00:00Z"}
    data.update(updates)
    return data


def result(values, columns=None):
    return {"series": [{"columns": columns or ["time", "value"], "values": values}]}


class PolicyTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock()
        self.client.query.return_value = {}
        self.policy = Policy(config(), self.client, ("dummy-secret",))

    def test_catalog_does_not_query(self):
        self.assertIn("entities", self.policy.call("catalog", {}))
        self.client.query.assert_not_called()

    def test_fixed_query_and_nanoseconds(self):
        stamp = "2024-10-01T00:00:00.123456789Z"
        self.client.query.return_value = result([[stamp, 1]])
        data = self.policy.call("sample", arguments(limit=1))
        self.assertEqual(data["rows"][0]["time"], stamp)
        query = self.client.query.call_args.args[0]
        self.assertIn('"homeassistant"."autogen"."state"', query)
        self.assertIn('"value"::field', query)
        self.assertIn('"entity_id" = \'office_ceiling\'', query)
        self.assertTrue(query.endswith("ASC LIMIT 2"))
        self.assertNotIn(";", query)

    def test_injection_unknown_and_invalid_inputs(self):
        bad = [{"entity_id": "light.x'; DROP DATABASE x"}, {"retention_policy": "autogen; DROP DATABASE x"},
               {"fields": ["password"]}, {"fields": ["value; DELETE"]}, {"fields": ["value", "value"]},
               {"fields": {}}, {"order": []}, {"limit": True}, {"limit": 501}, {"limit": 0},
               {"q": "DROP DATABASE x"}, {"database": "other"}, {"start": "2024-10-01"},
               {"end": "2024-10-03T00:00:00Z"}, {"end": "2024-10-01T00:00:00Z"},
               {"end": "2200-01-01T00:00:00Z"}]
        for update in bad:
            with self.subTest(update=update), self.assertRaises(SafeError):
                self.policy.call("sample", arguments(**update))
        self.client.query.assert_not_called()

    def test_unknown_tool(self):
        with self.assertRaises(SafeError):
            self.policy.call("query", {"q": "SELECT *"})

    def test_truncation_and_coverage(self):
        self.client.query.return_value = result([["2024-10-01T00:00:01Z", 1], ["2024-10-01T00:00:02Z", 2]])
        data = self.policy.call("coverage_window", arguments(limit=1))
        self.assertTrue(data["truncated"])
        self.assertFalse(data["window_complete"])
        self.assertEqual(data["non_null_returned"], 1)
        self.assertEqual(data["last_returned"], "2024-10-01T00:00:01Z")

    def test_empty_window(self):
        data = self.policy.call("sample", arguments())
        self.assertEqual(data["rows"], [])
        self.assertIn("empty does not prove inactivity", data["scope"])

    def test_nanosecond_bounds_are_exact(self):
        self.assertEqual(instant("2024-10-01T00:00:00.000000002Z")
                         - instant("2024-10-01T00:00:00.000000001Z"), 1)
        self.client.query.return_value = result([["2024-10-01T00:00:00.000000002Z", 1]])
        with self.assertRaises(SafeError):
            self.policy.call("sample", arguments(start="2024-10-01T00:00:00.000000001Z",
                                                 end="2024-10-01T00:00:00.000000002Z"))

    def test_timezone_equivalence(self):
        self.assertEqual(instant("2024-09-30T20:00:00-04:00"), instant("2024-10-01T00:00:00Z"))

    def test_out_of_order_and_out_of_window(self):
        for values in [[["2024-10-01T01:00:00Z", 1]],
                       [["2024-10-01T00:00:02Z", 1], ["2024-10-01T00:00:01Z", 2]]]:
            self.client.query.return_value = result(values)
            with self.assertRaises(SafeError):
                self.policy.call("sample", arguments())

    def test_multiple_series_and_extra_rows_refused(self):
        self.client.query.return_value = {"series": [result([])["series"][0]] * 2}
        with self.assertRaises(SafeError):
            self.policy.call("sample", arguments())
        self.client.query.return_value = result([["2024-10-01T00:00:01Z", 1]] * 3)
        with self.assertRaises(SafeError):
            self.policy.call("sample", arguments(limit=1))

    def test_projection_redaction_and_value_bounds(self):
        self.client.query.return_value = result([["2024-10-01T00:00:01Z", "contains dummy-secret", "private"]],
                                                 ["time", "value", "unapproved"])
        data = self.policy.call("sample", arguments())
        self.assertEqual(data["rows"][0]["value"], "[redacted]")
        self.assertNotIn("unapproved", data["rows"][0])
        for value in ["x" * 257, {}, float("inf")]:
            self.client.query.return_value = result([["2024-10-01T00:00:01Z", value]])
            with self.assertRaises(SafeError):
                self.policy.call("sample", arguments())

    def test_rate_and_concurrency(self):
        self.policy.config.rate = 1
        self.policy.call("sample", arguments())
        with self.assertRaises(SafeError):
            self.policy.call("sample", arguments())
        self.policy.lock.acquire()
        try:
            with self.assertRaises(SafeError):
                self.policy.call("sample", arguments())
        finally:
            self.policy.lock.release()
        self.assertEqual(self.client.query.call_count, 1)

    def test_metadata_is_projected(self):
        self.client.query.return_value = result([["value", "float"], ["secret_token", "string"]], ["fieldKey", "fieldType"])
        self.assertEqual(self.policy.call("fields", {"entity_id": "light.office_ceiling"})["fields"],
                         [{"name": "value", "type": "float"}])
        self.client.query.return_value = result([["autogen", "0s", "hidden"], ["other", "1h", "hidden"]],
                                                 ["name", "duration", "private"])
        data = self.policy.call("retention_policies", {})
        self.assertEqual(data["policies"], [{"name": "autogen", "duration": "0s"}])


class ValidationTests(unittest.TestCase):
    def test_config_security(self):
        for update in [{"influx_url": "http://localhost:8086"}, {"influx_url": "https://user:pass@localhost"},
                       {"influx_url": "https://localhost/query"}, {"database": 'x"; DELETE'},
                       {"max_rows": True}, {"unknown": 1}, {"allowed_hosts": ["*"]},
                       {"entities": {"sensor.x": {"measurement": "state", "fields": ["api_key"]}}}]:
            with self.subTest(update=update), self.assertRaises(SafeError):
                config(**update)
        self.assertEqual(config(influx_url="http://localhost:8086", allow_plaintext_influx=True).url,
                         "http://localhost:8086")

    def test_strict_json(self):
        for raw in [b'{"x":1,"x":2}', b'{"x":NaN}', b'no', b'\xff']:
            with self.assertRaises(SafeError):
                strict_json(raw)

    def test_bad_rows(self):
        for data in [{"series": [{}]}, result([[1]], ["time", "value"]), result([[1, 2]], ["x", "x"]),
                     {"series": [{"partial": True}]}]:
            with self.assertRaises(SafeError):
                list(rows(data))

    def test_secret_permissions_symlink_and_fifo(self):
        # Temporary dummy data only, removed by TemporaryDirectory on exit.
        with tempfile.TemporaryDirectory(prefix="influx-mock-", dir=__import__("os").environ.get("DWELLMIND_TEST_TMPDIR")) as directory:
            path = Path(directory) / "dummy"
            path.write_text("dummy-test-value\n")
            path.chmod(0o600)
            self.assertEqual(read_secret(path), "dummy-test-value")
            path.chmod(0o644)
            with self.assertRaises(SafeError):
                read_secret(path)
            path.chmod(0o600)
            link = Path(directory) / "link"
            link.symlink_to(path)
            with self.assertRaises(SafeError):
                read_secret(link)
            fifo = Path(directory) / "fifo"
            os.mkfifo(fifo, 0o600)
            with self.assertRaises(SafeError):
                read_secret(fifo)
            with patch("upstream.os.geteuid", return_value=os.geteuid() + 1), self.assertRaises(SafeError):
                read_secret(path)


class Response(io.BytesIO):
    status = 200

    def __init__(self, raw):
        super().__init__(raw)
        self.headers = Message()
        self.headers["Content-Type"] = "application/json"


class UpstreamTests(unittest.TestCase):
    def setUp(self):
        self.client = InfluxClient(config(), "dummy-user", "dummy-password")
        self.client.opener = Mock()

    def test_fixed_endpoint_credentials_only_in_header(self):
        self.client.opener.open.return_value = Response(b'{"results":[{}]}')
        self.assertEqual(self.client.query("SHOW RETENTION POLICIES"), {})
        req = self.client.opener.open.call_args.args[0]
        self.assertNotIn("dummy-password", req.full_url)
        self.assertEqual(req.method, "GET")
        self.assertTrue(req.get_header("Authorization").startswith("Basic "))

    def test_error_redaction(self):
        self.client.opener.open.side_effect = HTTPError("https://example", 401, "dummy-password", {}, None)
        with self.assertRaises(SafeError) as caught:
            self.client.query("fixed query")
        self.assertNotIn("dummy-password", str(caught.exception))

    def test_malformed_partial_and_oversized(self):
        for raw in [b'{"error":"dummy-password"}', b'{"results":[{"partial":true}]}',
                    b'{"results":[{"error":"dummy-password"}]}', b'{"results":[]}', b'x' * 524289]:
            self.client.opener.open.return_value = Response(raw)
            with self.assertRaises(SafeError) as caught:
                self.client.query("fixed query")
            self.assertNotIn("dummy-password", str(caught.exception))

    def test_compression_redirects_and_proxies(self):
        response = Response(b'{"results":[{}]}')
        response.headers["Content-Encoding"] = "gzip"
        self.client.opener.open.return_value = response
        with self.assertRaises(SafeError):
            self.client.query("fixed query")
        with self.assertRaises(SafeError):
            NoRedirects().redirect_request(None, None, 302, "", {}, "https://elsewhere")
        with patch.dict(os.environ, {"https_proxy": "http://bad.example:8080"}):
            client = InfluxClient(config(), "dummy-user", "dummy-password")
            self.assertFalse(any(getattr(h, "proxies", {}) for h in client.opener.handlers))


class MCPTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy(config(), Mock())
        self.mcp = MCP(self.policy)

    def call(self, method, params=None):
        return self.mcp.dispatch({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})

    def test_handshake_list_ping_notifications(self):
        self.assertEqual(self.call("initialize")["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(len(self.call("tools/list")["result"]["tools"]), 5)
        self.assertEqual(self.call("ping")["result"], {})
        self.assertIsNone(self.mcp.dispatch({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    def test_tool_and_internal_errors(self):
        self.assertTrue(self.call("tools/call", {"name": "query"})["result"]["isError"])
        self.policy.client.query.side_effect = RuntimeError("dummy-password")
        reply = self.call("tools/call", {"name": "sample", "arguments": arguments()})
        self.assertEqual(reply["error"]["code"], -32603)
        self.assertNotIn("dummy-password", json.dumps(reply))

    def test_batch_bounds_and_output_limit(self):
        with self.assertRaises(SafeError):
            self.mcp.dispatch([[]])
        with self.assertRaises(SafeError):
            self.mcp.dispatch([{}] * 5)
        with patch.object(self.policy, "call", return_value={"large": "x" * 40000}):
            self.assertTrue(self.call("tools/call", {"name": "catalog"})["result"]["isError"])

    def test_http_auth_host_origin_framing(self):
        import hashlib
        handler = object.__new__(Handler)
        handler.server = Mock(hosts={"localhost:8788"}, token_digest=hashlib.sha256(b"dummy-token").digest(), mcp=self.mcp)
        handler.path = "/mcp"
        handler._reply = Mock()
        def headers(extra=(), auth="Bearer dummy-token", host="localhost:8788"):
            handler.headers = Message()
            handler.headers["Host"] = host
            handler.headers["Authorization"] = auth
            for k, v in extra:
                handler.headers[k] = v
        for extra, auth, host, status in [([], "Bearer wrong", "localhost:8788", 401),
                                        ([], "Bearer dummy-token", "evil.example", 403),
                                        ([("Origin", "https://evil.example")], "Bearer dummy-token", "localhost:8788", 403),
                                        ([("Host", "localhost:8788")], "Bearer dummy-token", "localhost:8788", 403)]:
            headers(extra, auth, host)
            self.assertFalse(handler._authorized())
            self.assertEqual(handler._reply.call_args.args[0], status)
        body = b'{"jsonrpc":"2.0","id":1,"method":"ping"}'
        headers([("Content-Type", "application/json"), ("Content-Length", str(len(body)))])
        handler.rfile = io.BytesIO(body)
        handler.do_POST()
        self.assertEqual(handler._reply.call_args.args[0], 200)
        for extra in [[("Content-Length", str(MAX_INPUT + 1)), ("Content-Type", "application/json")],
                      [("Content-Length", "1"), ("Transfer-Encoding", "chunked"), ("Content-Type", "application/json")]]:
            headers(extra)
            handler.rfile = io.BytesIO(b"x")
            handler.do_POST()
            self.assertEqual(handler._reply.call_args.args[0], 400)


if __name__ == "__main__":
    unittest.main()
