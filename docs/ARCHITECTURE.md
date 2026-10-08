# Architecture and evidence boundaries

```text
Home Assistant WebSocket API
  ├─ subscribe_entities (explicit selected room IDs)
  └─ optional automation/script events
       ↓
bounded transport → compressed-state decoder → passive projection
       ↓                                      ↓
unknown gap markers                     private journal
                                               ↓
                                         offline summary

InfluxDB 1.x (optional, separate reader)
       ↓
fixed-query allowlist → bounded historical samples → offline room baseline
```

## Capture is not control

`ha_live_observer.validate_command` is the sole application send gateway and
allows auth, explicit entity observation, two optional causal event types and
ping only. It has no device-control command path. Account permissions remain
independent: a normal HA token may authorize actions that this code never sends.
There is no claim of a security sandbox around a compromised Python process.

Snapshot/added-state records have `record_kind=snapshot`. Incremental diffs have
`state_update`; removals and transport gaps are explicit. A snapshot of an on
light is not a person turning it on. A light state update is not necessarily a
command, nor a desired preference. Initial context can be stale, so it is never
converted into corrective feedback.

## Time and availability

Journals use monotonically ordered capture-receipt timestamps. HA last-update
timestamps are retained separately as `source_time`; these do not establish an
actuation instant. Reconnection resets state and context, with a fresh atomic
entity snapshot. Entities absent from that snapshot stay unknown.

The summary measures reported HA state evidence inside the observed interval.
It does not independently verify physical-device health or real human occupancy.
If disk fills, the final gap may be unwritable; EOF ends evidence instead of
extending it indefinitely. Some historical archives omit unknown/unavailable
rows entirely, which is why archived silence is not availability evidence.

## Attribution and privacy

Actor classes: `automation`, `script`, `engine`, `user_associated`, `unattributed`.
Engine provenance propagates through a known descendant script. Missing or
denied causal evidence stays unattributed; it never implies manual input.

The decoder retains selected states, bounded numeric light attributes and limited
context in memory. User IDs reduce to a boolean. Causal context IDs never reach
journals; the causal map expires after 120 seconds and holds at most 512 entries.
The selected-entity state cache retains current context until replaced/reset.
Media titles/content URLs, arbitrary attributes and raw upstream errors are not
saved. Journals still reveal room activity and must remain private.

## Learning scope

`office_report.py` is the historical analyzer, shared by room profiles. Its
routine baseline uses only reconstructed PIR/mmWave activity, a Beta-smoothed
hour-of-day estimate and chronological expanding-window evaluation. It compares
against a constant-prevalence baseline using Brier score. It is not neural, not
an LLM, and not a trained lighting-preference controller.

Derived occupancy and actuator states are context, never ground-truth occupancy
or preferences. Day starts remain unknown without carry-in; a configurable hold
assumption limits extrapolation. Sensitivity analysis is essential. Short samples,
correlated bins, pets, sensor latency and selection bias limit conclusions.

No learning output actuates anything. Future control requires independent feedback,
manual-override priority, nighttime limits, equipment limits and multiweek shadow
validation. These future safeguards are not advertised as already implemented.

## Packaging

The public container ships live observation and reporting; the Influx MCP reader
is an optional source tool, not a second service automatically started by the
Unraid template. The runtime has no listening port and no HA config access.
Default root entry is limited to initializing an empty appdata mount and dropping
to non-root before network IO. Existing contents are never recursively modified.

Reviewed upstream formats:
- [HA 2026.9.4 WebSocket commands](https://github.com/home-assistant/core/blob/2026.9.4/homeassistant/components/websocket_api/commands.py)
- [HA compressed state diffs](https://github.com/home-assistant/core/blob/2026.9.4/homeassistant/components/websocket_api/messages.py)
- [HA SSE administrator restriction](https://github.com/home-assistant/core/blob/2026.9.4/homeassistant/components/api/__init__.py)
- [websocket-client 1.9.2 framing](https://github.com/websocket-client/websocket-client/blob/v1.9.2/websocket/_abnf.py)
