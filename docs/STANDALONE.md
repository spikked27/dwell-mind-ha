# Install DwellMind HA on Unraid

This alpha is a bounded observation job, not an always-on daemon. Start with the
five-minute default and inspect its output before longer captures.

## 1. Confirm an image is available

The repository's **Publish container** workflow publishes `edge` for `main`, a
commit-SHA tag, and `v*` release tags to GHCR after tests and a container smoke
check. There is deliberately no `latest` tag suggesting production stability.

Before installing, check **Actions → Publish container** succeeded and the
package can be pulled without login. The repository owner may need to set the
GHCR package visibility to **Public**; public source does not guarantee public
package visibility.

If no package exists yet, build on Unraid:

```sh
git clone https://github.com/spikked27/dwell-mind-ha.git /mnt/user/appdata/dwellmind-ha-source
cd /mnt/user/appdata/dwellmind-ha-source
docker build -t ghcr.io/spikked27/dwell-mind-ha:edge .
```

Keep source and writable appdata separate. The source directory is not the
`/mnt/user/appdata/dwellmind-ha` capture directory.

## 2. Install the user template

From Unraid's terminal (after the source is on the `main` branch):

```sh
mkdir -p /boot/config/plugins/dockerMan/templates-user
curl --fail --location \
  https://raw.githubusercontent.com/spikked27/dwell-mind-ha/main/unraid/dwellmind-ha.xml \
  -o /boot/config/plugins/dockerMan/templates-user/my-dwellmind-ha.xml
```

Review the downloaded XML before installation. In **Docker → Add Container**,
choose the `dwellmind-ha` user template. A direct user template is not a claim of
Community Applications approval. No web UI port or privileged mode is required.

## 3. Configure locally

- **Appdata:** a new empty `/mnt/user/appdata/dwellmind-ha` directory.
- **PUID / PGID:** defaults `99` / `100` (Unraid nobody/users). Use your own
  dedicated non-root identity if preferred. Private journal files use 0600 and
  directories 0700; SMB access under other users may be intentionally blocked.
- **Home Assistant URL:** an origin such as `https://ha.example.internal:8123`,
  with a valid trusted certificate and no `/api` suffix.
- **HA token:** create a token for the dedicated HA observer account and enter it
  privately in the template. Do not post the token or saved XML to this repository.
- **Plaintext opt-in:** set `ALLOW_PLAINTEXT_HA=true` only if you deliberately
  accept sending the token over local `http://`/`ws://`. It defaults to false.
- **Rooms:** `office`, `living_room`, or `office,living_room`.
- **Entity roles:** replace every sample ID for selected rooms with your real
  entity ID from HA. These are profiles, not automatic HA area discovery.
- **Capture seconds:** retain `300` for the first test.

Current alpha profiles expect two distinct lights, PIR, mmWave, derived occupancy
and illuminance per room. Living Room TV is optional; clear its field to disable.
Do not use fictitious entities as evidence. If some roles do not exist, use only
a matching profile or request support for a reduced profile. Omitted/nonexistent
entities remain unknown and lower reported coverage.

### Token file alternative

To avoid storing a token value in Docker's environment/template:

1. Leave `HA_TOKEN` empty.
2. Create a private local file outside source control, mode 0600 and owned by PUID.
   `configure_observer_token.py --file /private/path/ha_observer_token --uid 99`
   can prompt locally without printing it (run as root when changing ownership).
3. Add a **read-only Path** mapping from that file to `/run/secrets/ha_observer_token`.
4. Add a variable `HA_TOKEN_FILE=/run/secrets/ha_observer_token`.

Exactly one token source must be configured. Host root and Docker administrators
can still read a mounted secret file; this is not protection from host administrators.

### Privileges

The image initially runs as root only to initialize an **empty** mount's owner
and permissions. It then drops supplementary groups, GID and UID before loading
the app or accessing the network. The template grants only CHOWN, FOWNER, SETUID
and SETGID for this setup; no privileged mode, host network, Docker socket, or
Home Assistant config mount is used. Root filesystem is read-only.

For an already prepared appdata directory, advanced users can remove the four
cap-add flags and add `--user=99:100`; the entry point skips root initialization.
An existing nonempty directory with incorrect owner/mode fails rather than changing
its contents recursively. Correct its permissions locally after verifying ownership.

## 4. Read the first result

Click **Apply**. After roughly five minutes, the container should stop. A zero
exit code means a bounded capture completed and at least one selected entity was
initialized, not that every sensor was available or the model was validated.

Logs contain only fixed errors and summary counters, including:
- `max_initialized_entities`: compare with selected roles (6 Office, 7 Living Room
  by default, fewer if optional media is disabled);
- `attribution_subscriptions_denied`: optional causal events denied by HA;
- `connection_failures`, `stop_reason`, receive attempts and row count;
- the private summary-file path.

Files on Unraid:

```text
/mnt/user/appdata/dwellmind-ha/
  captures/capture-<id>/events-01.jsonl
  reports/capture-<id>.json
```

The summary reports projected state coverage, explicit gaps and light-update
actor counts. Snapshots are not actions; a user-associated action is not a
verified correction. A gap is unknown, not unoccupied. No devices are controlled.

## 5. Longer runs and retention

You may increase duration up to 86400 seconds (one day), but receive-attempt,
reconnect and disk limits can end a run sooner. The container never silently
restarts. Keep Unraid auto-start off until the first capture is verified.

Each run has at most four 4 MiB journal files and one summary. There is a 64-run
directory cap. Old data is never automatically deleted: archive/remove it locally
or capture stops at the cap. Do not attach an unconditional restart policy;
authentication failures must not become an endless retry loop.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Image pull denied/not found | Workflow success, GHCR public visibility, exact tag; otherwise local build |
| No template listed | XML under `templates-user`, reload Docker page, select Add Container |
| Exits at five minutes with 0 | Expected bounded capture; read report |
| Configuration refused | URL is an origin; HTTP opt-in explicit; valid duration and entity domains |
| Appdata refused | New empty directory or verified PUID ownership and mode 0700 |
| Authentication rejected | Local token, account permissions, HA URL; never share token here |
| Optional attribution denied | Expected for some accounts; do not grant admin automatically |
| Zero/partial initialized entities | Wrong IDs, missing entities, permissions, unsupported protocol |
| Reconnect budget | Reachability, certificate trust, proxy fragmentation, HA version; no raw errors logged |
| Unknown journal interval | Capture gap/removal/invalid state; never fill as absence |

This template has automated XML/configuration tests. Actual Unraid UI rendering,
registry access, HA permission grants and network compatibility require installation
verification on your host.
