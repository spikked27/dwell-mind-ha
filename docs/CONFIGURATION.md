# Companion configuration

For the current worker, select rooms and entities in the [HA integration](INTEGRATION.md). The [Unraid template](UNRAID.md) contains infrastructure settings only.

## Retained standalone configuration

The following applies only to the explicit legacy CLI capture/historical tools.

# Configuration reference

## Container environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `HA_URL` | required | Fixed HTTPS HA origin; no path/query/credentials |
| `HA_TOKEN` | empty | Token supplied privately in template/environment |
| `HA_TOKEN_FILE` | empty | Absolute owner-only token path; exclusive with HA_TOKEN |
| `ALLOW_PLAINTEXT_HA` | `false` | Explicit `true` enables HTTP/WS; token then travels unencrypted |
| `ROOMS` | `office,living_room` | One or both profile keys |
| `CAPTURE_SECONDS` | `300` | 10–86400 seconds |
| `MAX_MESSAGES` | `10000` | 20–100000 receive attempts, including idle timeouts |
| `MAX_RECONNECTS` | `3` | 0–10 connection retries |
| `ENGINE_ENTITIES` | empty | Comma-separated automation/script IDs belonging to a learner |
| `DATA_DIR` | `/data` | Existing appdata mount; absolute path |
| `PUID` / `PGID` | `99` / `100` | Identity used after empty-mount initialization |

Token sources are mutually exclusive. The app never writes an environment token
to disk, but Docker/Unraid does retain it in container/template configuration.

## Room role fields

Use uppercase profile prefixes `OFFICE_` and `LIVING_ROOM_`:

| Suffix | Required domain | Purpose |
| --- | --- | --- |
| `PRIMARY_LIGHT` | `light` | Primary room light |
| `EXTRA_LIGHT` | `light` | Distinct secondary/accent light |
| `PIR` | `binary_sensor` | Physical motion evidence |
| `MMWAVE` | `binary_sensor` | Physical stationary-presence evidence |
| `DERIVED` | `binary_sensor` | Helper/combined occupancy, not a physical label |
| `LUX` | `sensor` | Numeric illuminance |
| `MEDIA` | `media_player` | Optional media state; blank disables |

For example `OFFICE_PRIMARY_LIGHT=light.study_ceiling`. All non-media roles are
required by the current profile shape. No duplicates within or across profiles.
See `.env.example` and the Unraid template for replaceable defaults. The Office
media role defaults off but can be added as an advanced environment variable.

## Standalone JSON configuration

Advanced users can run `ha_live_observer.py --config /private/live-observer.json`
directly instead of the container entry point. See `live-observer.example.json`.
It accepts `room_overrides`, a mapping from profile key to any room-role fields
(lowercase property names from `rooms.Room`, plus optional `name`).

Example fragment:

```json
{
  "rooms": ["office"],
  "room_overrides": {
    "office": {
      "primary_light": "light.study_ceiling",
      "extra_light": "light.study_lamp"
    }
  }
}
```

Merge this into the full example; it is not a complete configuration. Standalone
capture reads only a private token file, writes bounded journals and prints a
summary. `container_app.py` additionally generates an offline report automatically.

## Operational limits

- Fixed entity subscription, no wildcard or all-home `get_states`.
- Optional causal subscriptions: automation_triggered and script_started.
  These event types are not entity-scoped upstream: source/context metadata from
  other automations may transiently enter the bounded cache, but is only journaled
  when causally matched to a selected entity update. No raw causal events are saved.
- 256 KiB maximum incoming WebSocket frame, fragmented messages refused before
  payload assembly. This can reject some proxies; there is no unbounded fallback.
- Five-second socket timeout, ten-second per-message read budget checked between
  reads, 20-second heartbeat, ten-second outstanding pong budget.
- At most 512 expiring causal contexts; snapshot state cache only for selected IDs.
- Four 4 MiB journal files per capture. New files use exclusive creation.
- 64 capture directories across starts; no silent deletion or automatic retention.
- Duration is an internal budget, not an OS-enforced deadline for DNS/filesystem
  stalls. Consumers must treat abrupt EOF as observation ending.

The image needs outbound access to the reviewed HA origin (and DNS if used).
It listens on no port. No shell execution, service control, remote configuration
endpoint or external telemetry is exposed by DwellMind HA.
