# DwellMind HA

**Your home, learning your rhythm.**

Local-first room observation and experimental machine learning for Home Assistant.
Built to understand occupancy, lighting context and everyday routines without
confusing an automation's actions with a person's preferences.

> **Early alpha — observation only.** The current release records bounded,
> room-scoped evidence and produces private offline reports. It does not control
> devices, continuously retrain, provide a web dashboard or operate heating.
> DwellMind HA is an independent project, not an official Home Assistant product.

## What works today

- **Office and Living Room profiles**, with editable entity IDs—not hardcoded
  assumptions about your Home Assistant areas.
- **Scoped live observation** using HA WebSocket entity subscriptions, initial
  snapshots and incremental updates. No all-home state download.
- **Action provenance:** automation, script, engine, user-associated or unattributed.
  No action automatically becomes a preference-training label.
- **Availability and gap handling:** disconnects clear cached evidence and record
  unknown intervals. Silence is never interpreted as an empty room.
- **Private bounded journals** and an automatically generated JSON summary after
  each capture. Appdata stays on your host; there is no cloud analytics service.
- **Historical analysis tools** for bounded InfluxDB 1.x samples and offline
  reports, including an experimental hour-of-day sensor-routine baseline.
- **Unraid template and Docker packaging**, with configuration through template
  fields, environment variables or a locally mounted token file.

## Start on Unraid

Read **[Unraid installation](UNRAID.md)** for the template and first capture.

Image location after the publishing workflow succeeds:

```text
ghcr.io/spikked27/dwell-mind-ha:edge
```

Template:
[unraid/dwellmind-ha.xml](../unraid/dwellmind-ha.xml)

The template exposes HA URL/token, room entity IDs, capture duration and appdata.
**A default run stops after five minutes.** An exited container with exit code 0
is expected. Results appear in `appdata/reports/capture-<id>.json`; journals live
under `appdata/captures/capture-<id>/`.

This repository does not claim Community Applications listing or a published
image merely because the template exists. Check the repository's **Actions**
and **Packages** tabs first. Build locally if the image has not been published.

## Docker Compose

```sh
git clone https://github.com/spikked27/dwell-mind-ha.git
cd dwell-mind-ha
cp .env.example .env
chmod 600 .env
# Edit .env privately: HA_URL, HA_TOKEN, and actual entity IDs.
docker compose up
```

If no published image exists, build it locally first:

```sh
docker build -t ghcr.io/spikked27/dwell-mind-ha:edge .
```

An empty appdata directory is initialized to the chosen non-root UID/GID, mode
0700, before dropping privileges and opening any HA connection. Existing data
is never recursively chowned or deleted. See [configuration](CONFIGURATION.md)
for pre-owned mounts and strict non-root startup.

## Credentials and privacy

Use a dedicated HA account, preferably non-admin. **Read-command-only code does
not make a normal HA token read-only:** token permissions are controlled by HA.
Optional automation/script subscriptions may be denied; capture continues with
limited attribution rather than requiring administrator access.

Unraid token fields are masked **but not encrypted**. Values remain visible to
Docker/Unraid administrators and in saved templates. Do not share templates,
`.env`, `docker inspect`, journals or household timelines. A mounted owner-only
token file is also supported. Never put real credentials into GitHub issues.

HTTPS/WSS is the default. Local plaintext HTTP/WS needs an explicit opt-in and
transmits the HA token without encryption. No exposed listening ports are needed.

See [security policy](../SECURITY.md) and [architecture](ARCHITECTURE.md).

## Learning without copying mistakes

Historical light states describe what happened—not what somebody wanted.
Existing lux automations, TV effects, daylight, manual switches and pets all affect
the evidence. DwellMind keeps physical sensor evidence, derived occupancy, media
context and lighting separate.

The current historical baseline uses Beta-smoothed hour-of-day sensor activity,
chronological evaluation and a constant-prevalence comparison. It is exploratory,
not a verified human occupancy model. Light actions are never its labels. Neither
this model nor the live observer enables controls. Availability coverage and
independent feedback come before preference learning.

## Development

Python 3.13+; the live transport uses a hash-pinned `websocket-client` wheel.
Other functionality uses the standard library.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: --require-hashes -r requirements-live.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_release.py
```

Tests use synthetic fixtures and a loopback dummy HA server, not a real home.
The transport tests run when the optional dependency is installed. CI also builds
the image and checks startup permissions and configuration with networking disabled.
No household recordings or real credentials are included in the repository.

Reviewed protocol baseline: HA Core 2026.9.x compressed WebSocket entity events.
Other HA versions, proxies and actual Unraid installation require deployment
verification; unsupported formats fail closed. See [testing](TESTING.md).

## Project documents

- [Unraid installation](UNRAID.md)
- [Configuration reference](CONFIGURATION.md)
- [Architecture and limits](ARCHITECTURE.md)
- [Historical analysis](HISTORICAL.md)
- [Testing and release process](TESTING.md)
- [Initial publishing handoff](PUBLISHING.md)
- [Roadmap](../ROADMAP.md) · [Contributing](../CONTRIBUTING.md) · [Changelog](../CHANGELOG.md)

Licensed under the [MIT License](../LICENSE).
