"""Read-command-only HA WebSocket observer. No device-control command path.

Optional websocket-client dependency is required only on the separate capture
host. Real HA grants and protocol compatibility remain deployment checks.
"""
import argparse
import json
from pathlib import Path
import ssl
import time
from urllib.parse import urlsplit
import uuid

from office_collect import rfc3339_ns
from passive_observer import Observer
from policy import SafeError, integer, strict_json
from private_journal import Journal
from rooms import PROFILES, configured_profiles
from upstream import read_secret
from ws_states import StateDecoder

MAX_FRAME = 262144


class PermissionRejected(SafeError):
    pass


class LiveConfig:
    def __init__(self, data):
        required = {"ha_url", "ha_token_file", "journal_directory", "rooms"}
        optional = {"allow_plaintext_ha", "duration_seconds", "max_messages", "max_reconnects", "engine_entities", "room_overrides"}
        if not isinstance(data, dict) or not required <= data.keys() or data.keys() - required - optional:
            raise SafeError("Invalid live observer configuration.")
        try:
            url = urlsplit(data["ha_url"])
            port = url.port
        except (ValueError, TypeError):
            raise SafeError("Invalid HA origin.") from None
        plaintext = data.get("allow_plaintext_ha", False)
        if (type(plaintext) is not bool or url.scheme not in {"https", "http"} or not url.hostname
                or url.username is not None or url.password is not None or url.query or url.fragment
                or url.path not in {"", "/"} or (url.scheme == "http" and not plaintext)):
            raise SafeError("HTTPS HA origin required; HTTP needs separate explicit opt-in.")
        if port is not None:
            integer(port, 1, 65535)
        self.url = ("wss" if url.scheme == "https" else "ws") + "://" + url.netloc + "/api/websocket"
        for key in ("ha_token_file", "journal_directory"):
            if not isinstance(data[key], str) or not Path(data[key]).is_absolute():
                raise SafeError("Absolute private file/directory paths required.")
        self.token_file, self.journal_directory = data["ha_token_file"], data["journal_directory"]
        rooms = data["rooms"]
        if (not isinstance(rooms, list) or not 1 <= len(rooms) <= 2
                or any(not isinstance(r, str) or r not in PROFILES for r in rooms) or len(set(rooms)) != len(rooms)):
            raise SafeError("Explicit Office/Living Room profile selection required.")
        configured = configured_profiles(data.get("room_overrides"))
        self.profiles = [configured[r] for r in rooms]
        self.duration = integer(data.get("duration_seconds", 300), 10, 86400)
        self.messages = integer(data.get("max_messages", 10000), 20, 100000)
        self.reconnects = integer(data.get("max_reconnects", 3), 0, 10)
        self.engine_entities = data.get("engine_entities", [])
        if (not isinstance(self.engine_entities, list) or len(self.engine_entities) > 20
                or any(not isinstance(e, str) or not e.startswith(("automation.", "script."))
                       or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_." for c in e)
                       for e in self.engine_entities)):
            raise SafeError("Invalid engine source allowlist.")


def validate_command(message, entities):
    """The sole send gateway permits authentication, observation and heartbeat."""
    kind = message.get("type")
    if kind == "auth":
        valid = message.keys() == {"type", "access_token"} and isinstance(message["access_token"], str)
    elif kind == "subscribe_entities":
        valid = message.keys() == {"id", "type", "entity_ids"} and message["entity_ids"] == sorted(entities)
    elif kind == "subscribe_events":
        valid = message.keys() == {"id", "type", "event_type"} and message["event_type"] in {"automation_triggered", "script_started"}
    elif kind == "ping":
        valid = message.keys() == {"id", "type"}
    else:
        valid = False
    if not valid or (kind != "auth" and (type(message.get("id")) is not int or message["id"] < 1)):
        raise SafeError("Non-observation WebSocket command refused.")


def guarded_frame_buffer(base):
    """Pinned-library boundary: reject excessive or fragmented frames before payload allocation."""
    class BoundedFrames(base):
        def recv_length(self):
            super().recv_length()
            if self.length > MAX_FRAME or not self.header[0] or self.header[4] == 0:
                raise SafeError("Oversized or fragmented WebSocket frame refused.")
    return BoundedFrames


def connect(config):
    try:
        import websocket
        from websocket._abnf import frame_buffer
    except ImportError:
        raise SafeError("Optional websocket-client dependency is not installed on this host.") from None
    if websocket.__version__ != "1.9.2":
        raise SafeError("Use the reviewed websocket-client 1.9.2 transport dependency.")
    websocket.enableTrace(False)

    class BoundedSocket(websocket.WebSocket):
        def __init__(self, **options):
            super().__init__(**options)
            self.frame_buffer = guarded_frame_buffer(frame_buffer)(self._recv, False)

        def recv(self):
            self.read_deadline = min(getattr(self, "capture_deadline", float("inf")), time.monotonic() + 10)
            try:
                return super().recv()
            except websocket.WebSocketTimeoutException:
                raise TimeoutError("HA receive timeout.") from None

        def _recv(self, size):
            if time.monotonic() >= getattr(self, "read_deadline", float("inf")):
                raise SafeError("HA message read budget exceeded.")
            return super()._recv(size)

    # Never follow redirects or use environment proxy credentials.
    return websocket.create_connection(config.url, class_=BoundedSocket, timeout=5,
                                       redirect_limit=0, suppress_origin=True, http_no_proxy=["*"],
                                       sslopt={"cert_reqs": ssl.CERT_REQUIRED, "check_hostname": True})


class Capture:
    def __init__(self, config, token, journal, connector=connect, clock=time.monotonic, sleep=time.sleep, wall=time.time_ns):
        self.config, self.token, self.journal = config, token, journal
        self.connector, self.clock, self.sleep, self.wall = connector, clock, sleep, wall
        self.observer = Observer(config.profiles, config.engine_entities)
        self.decoder = StateDecoder(self.observer.entities)
        self.last_stamp = 0
        self.count = 0
        self.stats = {"connections": 0, "connection_failures": 0, "attribution_subscriptions_denied": 0,
                      "messages": 0, "rows": 0, "max_initialized_entities": 0,
                      "stop_reason": "duration", "control_enabled": False}

    def stamp(self):
        # Receipt order avoids assuming the HA and capture clocks are identical.
        self.last_stamp = max(self.wall(), self.last_stamp + 1)
        return rfc3339_ns(self.last_stamp)

    def write(self, row):
        self.journal.write(row)
        self.stats["rows"] += 1

    def gap(self, reason):
        self.decoder.reset()
        for row in self.observer.reset(self.stamp(), reason):
            self.write({**row, "record_kind": "gap"})

    def send(self, ws, message):
        validate_command(message, self.observer.entities)
        ws.send(json.dumps(message, allow_nan=False, separators=(",", ":")))

    def receive(self, ws):
        self.count += 1
        if self.count > self.config.messages:
            raise SafeError("Live message budget reached.")
        raw = ws.recv()
        if not isinstance(raw, str) or not raw or len(raw.encode()) > MAX_FRAME:
            raise SafeError("Invalid or oversized HA WebSocket response.")
        message = strict_json(raw)
        if not isinstance(message, dict):
            raise SafeError("Invalid HA response shape.")
        self.stats["messages"] += 1
        return message

    def handle(self, message):
        kind, identifier = message.get("type"), message.get("id")
        if kind == "result":
            if identifier not in {1, 2, 3}:
                raise SafeError("Unexpected subscription result.")
            if message.get("success") is not True:
                if identifier == 1:
                    raise PermissionRejected("Entity observation permission rejected.")
                self.stats["attribution_subscriptions_denied"] += 1
            return
        if kind == "pong":
            return
        if kind != "event" or identifier not in {1, 2, 3}:
            raise SafeError("Unexpected HA response type.")
        received = self.stamp()
        if identifier == 1:
            envelopes = self.decoder.decode(message.get("event"), received)
            self.stats["max_initialized_entities"] = max(self.stats["max_initialized_entities"], len(self.decoder.states))
            for envelope in envelopes:
                row = self.observer.process(envelope)
                if row:
                    self.write({**row, "record_kind": envelope["record_kind"],
                                "source_time": envelope.get("source_time"),
                                "time_basis": "capture receipt; source_time is HA last update, not action time"})
        else:
            original = message.get("event")
            expected = "automation_triggered" if identifier == 2 else "script_started"
            if not isinstance(original, dict) or original.get("event_type") != expected:
                raise SafeError("Unexpected causal event.")
            # Only ephemeral causal IDs/source names reach the Observer; no raw journal.
            self.observer.process({**original, "time_fired": received})

    def run(self):
        deadline = self.clock() + self.config.duration
        self.gap("reconnected")
        try:
            for attempt in range(self.config.reconnects + 1):
                if self.clock() >= deadline:
                    break
                ws = None
                try:
                    ws = self.connector(self.config)
                    ws.capture_deadline = deadline
                    self.stats["connections"] += 1
                    if self.receive(ws).get("type") != "auth_required":
                        raise SafeError("Expected HA authentication handshake.")
                    self.send(ws, {"type": "auth", "access_token": self.token})
                    if self.receive(ws).get("type") != "auth_ok":
                        self.stats["stop_reason"] = "authentication_rejected"
                        break  # No credential retry loop or permission elevation.
                    # Subscribe causal events first, then atomic entity snapshot/diffs.
                    self.send(ws, {"id": 2, "type": "subscribe_events", "event_type": "automation_triggered"})
                    self.send(ws, {"id": 3, "type": "subscribe_events", "event_type": "script_started"})
                    self.send(ws, {"id": 1, "type": "subscribe_entities", "entity_ids": sorted(self.observer.entities)})
                    next_ping, ping_id, pending_ping = self.clock() + 20, 100, None
                    while self.clock() < deadline:
                        if pending_ping is not None and self.clock() > pending_ping[1]:
                            raise SafeError("HA heartbeat budget exceeded.")
                        if self.clock() >= next_ping and pending_ping is None:
                            self.send(ws, {"id": ping_id, "type": "ping"})
                            pending_ping = (ping_id, self.clock() + 10)
                            ping_id += 1
                            next_ping = self.clock() + 20
                        try:
                            message = self.receive(ws)
                        except TimeoutError:
                            continue
                        if message.get("type") == "pong":
                            if pending_ping is None or message.get("id") != pending_ping[0]:
                                raise SafeError("Unexpected HA heartbeat reply.")
                            pending_ping = None
                        else:
                            self.handle(message)
                except PermissionRejected:
                    self.stats["stop_reason"] = "entity_permission_rejected"
                    self.gap("connection_lost")
                    break
                except Exception:
                    self.stats["connection_failures"] += 1
                    self.gap("connection_lost")
                    if self.count >= self.config.messages:
                        self.stats["stop_reason"] = "message_budget"
                        break
                    if attempt >= self.config.reconnects:
                        self.stats["stop_reason"] = "reconnect_budget"
                        break
                finally:
                    if ws is not None:
                        ws.close()
                if self.clock() >= deadline:
                    break
                self.sleep(min(2 ** attempt, 30, max(0, deadline - self.clock())))
                self.gap("reconnected")
        finally:
            # Capture end is also an unknown interval, never persistent evidence.
            self.gap("connection_lost")
        self.stats["receive_attempts"] = self.count
        return self.stats


def main():
    parser = argparse.ArgumentParser(description="Bounded read-only HA observer on the separate capture host")
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    journal = None
    try:
        with Path(args.config).open("rb") as handle:
            raw = handle.read(65537)
        if len(raw) > 65536:
            raise SafeError("Configuration byte budget exceeded.")
        config = LiveConfig(strict_json(raw))
        token = read_secret(config.token_file)
        if len(token) < 32:
            raise SafeError("Invalid HA token.")
        journal = Journal(config.journal_directory, "capture-" + uuid.uuid4().hex)
        stats = Capture(config, token, journal).run()
        print(json.dumps(stats, separators=(",", ":")))
        if stats["stop_reason"] in {"authentication_rejected", "entity_permission_rejected", "reconnect_budget"}:
            raise SystemExit("Capture stopped without a verified successful run; inspect the non-sensitive summary.")
    except Exception:
        raise SystemExit("Capture refused or stopped; check local setup and permissions. No payload or credential logged.") from None
    finally:
        if journal is not None:
            journal.close()


if __name__ == "__main__":
    main()
