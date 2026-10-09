# Unraid worker installation

DwellMind uses a Home Assistant custom integration plus an Unraid worker. No HA add-on is required. Rooms and entities are selected in HA, never in the Docker template.

## Install the template

Run in the Unraid terminal:

```sh
mkdir -p /boot/config/plugins/dockerMan/templates-user
curl --fail --location https://raw.githubusercontent.com/spikked27/dwell-mind-ha/main/unraid/dwellmind-ha.xml -o /boot/config/plugins/dockerMan/templates-user/my-dwellmind-ha.xml
```

Use **Docker → Add Container → dwellmind-ha**. Review the template before applying it. Existing installed containers do not automatically inherit a changed GitHub template: edit/recreate the container from the new template while retaining its appdata path.

Settings are infrastructure only: appdata, API host port (default 8128), PUID and PGID. No HA URL, HA token, room names or entity IDs belong here. If migrating from the standalone observer, remove its old HA_TOKEN environment field locally; do not share saved templates or docker inspect output.

Use a new empty appdata directory, or an existing directory already owned by your PUID with mode 0700. Initialization never recursively changes nonempty data. Existing captures and reports remain in place. Do not delete unavailable HA entities or history.

Limits: 1 GiB RAM, no additional swap, 2 CPUs, 32 processes, bounded Docker logs. The root filesystem is read-only. Initial root setup has only CHOWN, FOWNER, SETUID and SETGID; the worker drops to PUID:PGID before network access. No privileged mode, host networking, Docker socket or HA configuration mount. The single-process worker handles termination directly as PID 1; an extra root init process is unnecessary and would need KILL capability to signal the worker after UID drop.

The service stays running but idle until you start a capture from HA. Keep auto-start disabled until the first five-minute capture is verified. No restart loop is configured. A Docker health check tests API responsiveness; it does not claim healthy sensors or a validated model.

## Pair Home Assistant

The first startup creates `/data/service-token`, mode 0600, in private appdata. Read it locally in the Unraid terminal and paste it only into HA's pairing form:

```sh
docker exec --user 99:100 dwellmind-ha cat /data/service-token
```

Change 99:100 if you selected another PUID/PGID. This key authorizes the DwellMind worker only; it is not a Home Assistant token. Never post it in chat, screenshots, logs, issues or Git. It persists across container upgrades. To rotate it, stop the container, replace that file locally with a securely generated owner-only key, then remove/re-pair the HA integration; captured history stays intact.

See [HA integration installation](INTEGRATION.md). Use the worker URL reachable from HA, such as `http://unraid.example.internal:8128`. An HTTPS origin through a trusted local reverse proxy is recommended. Direct HTTP is available only with explicit consent in HA's setup form. Never publish port 8128 to the internet. Host port mappings bind according to your Unraid Docker settings; restrict firewall/network access to trusted clients.

The worker stores its key privately and never prints it in logs. It needs no HA access token: the integration forwards selected observations. Worker selections remain in memory and are restored by the integration after a restart; every capture saves its own selection privately.

## Five-minute observation verification

In HA, choose areas, review discovered entities, retain 300 seconds, then press **Start observation capture**. Capture completion is independent of HA remaining online. The worker stops the job after its timer, writes a private report, and stays available for the next job.

Check HA's worker status, selected entity count, capture gaps and observation rows. Reports remain on Unraid:

```text
/mnt/user/appdata/dwellmind-ha/captures/capture-<id>/selection.json
/mnt/user/appdata/dwellmind-ha/captures/capture-<id>/events-01.jsonl
/mnt/user/appdata/dwellmind-ha/reports/capture-<id>.json
```

Review coverage and gaps in the private report. The app offers HA setup/status/buttons and a Latest capture report entity with bounded summary attributes; a separate detailed model-report web GUI remains future work. Offline historical analysis remains available. There is no lighting or heating controller and no claimed preference learning from actions.

Each capture has bounded journals (four 4 MiB files) and a summary. At 64 capture directories, new capture jobs are refused until you archive history locally. No automatic data deletion. Queue overflow, delivery interruptions, missing snapshots and unavailable entities become uncertainty, never absence or preference labels.

An anonymous image-index/blob check is not an actual pull on your host. Verify registry/network access on Unraid:

```sh
docker pull ghcr.io/spikked27/dwell-mind-ha:edge
```

See [historical standalone observer instructions](STANDALONE.md) only if you intentionally use the old CLI workflow.

## Reading reports without terminal copying

Upgrade the container image while retaining the existing appdata path and pairing key. With the matching HA integration, the Latest capture report entity exposes its coverage summary automatically. Authorized HA MCP clients can read it; they do not need SSH access or a Docker socket mount. Historical reports remain owner-only and are never deleted by this feature. General Docker deployment/administration remains separate.

## LAN access boundary

The worker accepts RFC1918 LAN, loopback and IPv6 unique-local source addresses only, in addition to its pairing authentication. Keep the published port bound/reachable only on the trusted LAN. Do not expose it through internet port forwarding or a public tunnel. A reverse proxy appears as the peer, so its own access policy must also be private. Forwarded client-IP headers are not trusted by the worker. HTTPS through a trusted local reverse proxy is recommended; direct HTTP still requires explicit consent.

Capture-summary access uses the authenticated GET endpoint and HA report entity. No Docker administration socket, shell endpoint or arbitrary file-reading capability is added.
