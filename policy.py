"""Fixed-query, bounded InfluxDB policy. No arbitrary InfluxQL interface."""
import json
import math
import re
import threading
import time
from collections import deque
from datetime import datetime, timezone
from urllib.parse import urlsplit

MAX_UPSTREAM = 524288
MAX_OUTPUT = 262144


class SafeError(Exception):
    """Only fixed, non-sensitive messages may reach clients."""


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise SafeError("Duplicate JSON key.")
            result[key] = value
        return result

    def invalid(value):
        raise SafeError("Non-finite JSON number.")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)
    except (ValueError, UnicodeError, RecursionError):
        raise SafeError("Invalid JSON.") from None


def identifier(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 128
            or any(c in value for c in "\\\"';") or any(ord(c) < 32 for c in value)):
        raise SafeError("Invalid configured identifier.")
    return '"' + value + '"'


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise SafeError("Integer outside allowed bounds.")
    return value


def instant(value):
    """Parse RFC3339 into exact integer nanoseconds, including query bounds."""
    if not isinstance(value, str):
        raise SafeError("Timezone-aware RFC3339 timestamp required.")
    match = re.fullmatch(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,9}))?(Z|[+-]\d\d:\d\d)", value)
    if not match:
        raise SafeError("Timezone-aware RFC3339 timestamp required.")
    try:
        dt = datetime.fromisoformat(match[1] + match[3].replace("Z", "+00:00"))
        dt = dt.astimezone(timezone.utc)
        if not 2000 <= dt.year <= 2200:
            raise ValueError
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        delta = dt - epoch
        return (delta.days * 86400 + delta.seconds) * 10**9 + int((match[2] or "").ljust(9, "0"))
    except ValueError:
        raise SafeError("Invalid timestamp.") from None


class Config:
    def __init__(self, data):
        required = {"influx_url", "database", "username_file", "password_file", "mcp_token_file",
                    "retention_policies", "entities", "allowed_hosts"}
        optional = {"allow_plaintext_influx", "listen_host", "listen_port", "query_timeout_seconds",
                    "max_window_hours", "max_rows", "queries_per_minute"}
        if not isinstance(data, dict) or not required <= data.keys() or data.keys() - required - optional:
            raise SafeError("Invalid configuration keys.")
        self.database = data["database"]
        identifier(self.database)
        try:
            url = urlsplit(data["influx_url"])
            port = url.port
        except (ValueError, TypeError):
            raise SafeError("Invalid upstream origin.") from None
        plaintext = data.get("allow_plaintext_influx", False)
        if (type(plaintext) is not bool or url.scheme not in {"https", "http"} or not url.hostname
                or url.username is not None or url.password is not None or url.query or url.fragment
                or url.path not in {"", "/"} or (url.scheme == "http" and not plaintext)):
            raise SafeError("Fixed HTTPS origin required; HTTP requires explicit opt-in.")
        if port is not None:
            integer(port, 1, 65535)
        self.url = data["influx_url"].rstrip("/")
        self.listen_host = data.get("listen_host", "127.0.0.1")
        if self.listen_host not in {"127.0.0.1", "0.0.0.0"}:
            raise SafeError("Unsupported listen address.")
        self.listen_port = integer(data.get("listen_port", 8788), 1024, 65535)
        self.timeout = integer(data.get("query_timeout_seconds", 5), 1, 10)
        self.window_hours = integer(data.get("max_window_hours", 24), 1, 24)
        self.max_rows = integer(data.get("max_rows", 200), 1, 500)
        self.rate = integer(data.get("queries_per_minute", 12), 1, 30)
        self.hosts = data["allowed_hosts"]
        if (not isinstance(self.hosts, list) or not 1 <= len(self.hosts) <= 10
                or any(not isinstance(h, str) or not re.fullmatch(r"[a-zA-Z0-9.-]+(?::[0-9]{1,5})?", h)
                       for h in self.hosts)):
            raise SafeError("Exact Host allowlist required.")
        self.policies = data["retention_policies"]
        if not isinstance(self.policies, list) or not 1 <= len(self.policies) <= 10:
            raise SafeError("Invalid retention-policy allowlist.")
        for policy in self.policies:
            identifier(policy)
        self.entities = data["entities"]
        if not isinstance(self.entities, dict) or not 1 <= len(self.entities) <= 40:
            raise SafeError("One to forty approved entities required.")
        for entity, spec in self.entities.items():
            if (not isinstance(entity, str) or not re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z0-9_]+", entity)
                    or not isinstance(spec, dict) or spec.keys() != {"measurement", "fields"}):
                raise SafeError("Invalid entity mapping.")
            identifier(spec["measurement"])
            fields = spec["fields"]
            if (not isinstance(fields, list) or not 1 <= len(fields) <= 12
                    or any(not isinstance(f, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", f)
                           or re.search(r"password|secret|token|api_?key|authorization|cookie", f, re.I) for f in fields)
                    or len(set(fields)) != len(fields)):
                raise SafeError("Invalid field allowlist.")
        self.secret_paths = [data[k] for k in ("username_file", "password_file", "mcp_token_file")]
        if any(not isinstance(p, str) or not p.startswith("/") for p in self.secret_paths):
            raise SafeError("Absolute secret paths required.")


def rows(result):
    series = result.get("series", [])
    if not isinstance(series, list) or len(series) > 100:
        raise SafeError("Invalid series response.")
    for item in series:
        if not isinstance(item, dict) or item.get("partial"):
            raise SafeError("Invalid or partial series.")
        columns, values = item.get("columns"), item.get("values", [])
        if (not isinstance(columns, list) or len(columns) > 100
                or any(not isinstance(c, str) for c in columns) or len(set(columns)) != len(columns)
                or not isinstance(values, list)):
            raise SafeError("Invalid response columns.")
        for row in values:
            if not isinstance(row, list) or len(row) != len(columns):
                raise SafeError("Invalid response row.")
            yield dict(zip(columns, row))


class Policy:
    def __init__(self, config, client, secrets=()):
        self.config, self.client = config, client
        self.secrets = tuple(s for s in secrets if s)
        self.lock, self.query_times = threading.Lock(), deque()

    def query(self, query):
        if not self.lock.acquire(blocking=False):
            raise SafeError("Query already running.")
        try:
            now = time.monotonic()
            while self.query_times and now - self.query_times[0] >= 60:
                self.query_times.popleft()
            if len(self.query_times) >= self.config.rate:
                raise SafeError("Query rate limit reached.")
            self.query_times.append(now)
            return self.client.query(query)
        finally:
            self.lock.release()

    def clean(self, value):
        if value is None or type(value) in {bool, int}:
            return value
        if type(value) is float and math.isfinite(value):
            return value
        if isinstance(value, str) and len(value) <= 256:
            return "[redacted]" if any(s in value for s in self.secrets) else value
        raise SafeError("Unsupported or oversized value.")

    def call(self, name, args):
        allowed = {"catalog": set(), "retention_policies": set(), "fields": {"entity_id"},
                   "sample": {"entity_id", "retention_policy", "fields", "start", "end", "limit", "order"},
                   "coverage_window": {"entity_id", "retention_policy", "field", "start", "end", "limit"}}
        if (not isinstance(name, str) or name not in allowed or not isinstance(args, dict)
                or args.keys() - allowed[name]):
            raise SafeError("Unknown tool or argument; arbitrary queries refused.")
        if name == "catalog":
            return {"entities": self.config.entities, "retention_policies": self.config.policies,
                    "limits": {"hours": self.config.window_hours, "rows": self.config.max_rows,
                               "queries_per_minute": self.config.rate}, "scope": "Configured, not verified archive schema."}
        if name == "retention_policies":
            records = list(rows(self.query("SHOW RETENTION POLICIES ON " + identifier(self.config.database))))
            if len(records) > 100:
                raise SafeError("Too many policies.")
            keys = {"name", "duration", "shardGroupDuration", "replicaN", "default"}
            return {"policies": [{k: self.clean(v) for k, v in r.items() if k in keys
                                  and r.get("name") in self.config.policies} for r in records
                                 if r.get("name") in self.config.policies],
                    "scope": "Retention duration does not prove oldest data or continuity."}
        entity = args.get("entity_id")
        if not isinstance(entity, str) or entity not in self.config.entities:
            raise SafeError("Entity not approved.")
        spec = self.config.entities[entity]
        if name == "fields":
            records = list(rows(self.query("SHOW FIELD KEYS ON " + identifier(self.config.database)
                                          + " FROM " + identifier(spec["measurement"]))))
            if len(records) > 2000:
                raise SafeError("Too many field definitions.")
            return {"fields": [{"name": r["fieldKey"], "type": self.clean(r.get("fieldType"))}
                               for r in records if isinstance(r.get("fieldKey"), str)
                               and r["fieldKey"] in spec["fields"]], "scope": "Measurement metadata, not entity coverage."}
        rp = args.get("retention_policy")
        if not isinstance(rp, str) or rp not in self.config.policies:
            raise SafeError("Retention policy not approved.")
        start, end = instant(args.get("start")), instant(args.get("end"))
        if not 0 < end - start <= self.config.window_hours * 3600 * 10**9 or end > time.time_ns():
            raise SafeError("Window outside permitted bounds.")
        limit = integer(args.get("limit", self.config.max_rows), 1, self.config.max_rows)
        order = args.get("order", "oldest")
        if not isinstance(order, str) or order not in {"oldest", "newest"}:
            raise SafeError("Invalid order.")
        fields = [args.get("field", "value")] if name == "coverage_window" else args.get("fields", ["value"])
        if (not isinstance(fields, list) or not 1 <= len(fields) <= 12
                or any(not isinstance(f, str) or f not in spec["fields"] for f in fields)
                or len(set(fields)) != len(fields)):
            raise SafeError("Field not approved.")
        domain, object_id = entity.split(".")
        source = ".".join(identifier(v) for v in [self.config.database, rp, spec["measurement"]])
        query = ("SELECT " + ",".join(identifier(f) + "::field" for f in fields) + " FROM " + source
                 + f' WHERE "domain" = \'{domain}\' AND "entity_id" = \'{object_id}\''
                 + f" AND time >= {start} AND time < {end} ORDER BY time "
                 + ("ASC" if order == "oldest" else "DESC") + " LIMIT " + str(limit + 1))
        response = self.query(query)
        if len(response.get("series", [])) > 1:
            raise SafeError("Unexpected multiple series.")
        records, previous = [], None
        for row in rows(response):
            if len(records) >= limit + 1:
                raise SafeError("Upstream row limit exceeded.")
            t = instant(row.get("time"))
            if not row["time"].endswith("Z") or not start <= t < end:
                raise SafeError("Out-of-window upstream timestamp.")
            if previous is not None and ((order == "oldest" and t < previous) or (order == "newest" and t > previous)):
                raise SafeError("Unexpected upstream ordering.")
            previous = t
            records.append({"time": row["time"], **{f: self.clean(row.get(f)) for f in fields}})
        truncated = len(records) > limit
        records = records[:limit]
        result = {"entity_id": entity, "returned_rows": len(records), "truncated": truncated,
                  "window_complete": not truncated, "start": args["start"], "end": args["end"], "order": order,
                  "scope": "Selected-field rows only, no carry-in state; empty does not prove inactivity."}
        if name == "sample":
            result["rows"] = records
        else:
            ordered = sorted(records, key=lambda r: instant(r["time"]))
            result.update(first_returned=ordered[0]["time"] if ordered else None,
                          last_returned=ordered[-1]["time"] if ordered else None,
                          non_null_returned=sum(r[fields[0]] is not None for r in records))
        return result
