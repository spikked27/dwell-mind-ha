# Home Assistant companion integration

Use a **custom integration in Home Assistant** and the **Docker worker on Unraid**. No HA add-on is required. The worker provides an authenticated local API; HA provides native room/entity selectors, capture controls and status. No HA credential is sent to Unraid.

## Install

This repository is structured for HACS as a custom integration repository; it is not a claim of inclusion in the default HACS catalog. In HACS, add `https://github.com/spikked27/dwell-mind-ha` as a **custom repository**, category **Integration**, then install **DwellMind HA** and restart HA.

Alternatively, download the repository and copy only `custom_components/dwellmind/` to `/config/custom_components/dwellmind/` on your HA host. Preserve existing custom integrations. Restart HA after copying. Never edit HA internal storage/database files.

The integration is tested against Home Assistant 2026.9.4 in CI. Live HA loading, UI rendering, LAN reachability and Unraid runtime still require an actual installation test. HACS installation itself is not verified by those unit tests.

## Setup and use

1. Install/start the [Unraid worker](UNRAID.md).
2. In HA, open **Settings → Devices & services → Add integration → DwellMind HA**.
3. Enter the worker URL and its private pairing key. Use HTTPS through a local reverse proxy where possible. HTTP requires explicit consent because observations and the worker key travel unencrypted. Never expose the API publicly.
4. Choose up to eight HA Areas using the native room picker.
5. Review the discovered entities using HA's native entity picker. Choose at most 40 across all selected rooms. Keep 300 seconds for the first capture.
6. Open the DwellMind device and press **Start observation capture**. Setup alone does not start collection.
7. Review worker status, selected rooms/entities, observation rows and capture gaps. The **Latest capture report** entity exposes a bounded summary in its attributes: duration, coverage per entity, zero-coverage entities, attribution counts and terminal-gap status. Full reports/journals remain private on Unraid. The existing HA MCP connection can read this entity without receiving a worker key or requiring SSH.

Use the integration's **Configure** action to change rooms/entities. Stop an active capture first. No deletion or reassignment of existing HA entities occurs. Newly assigned entities are not silently subscribed: rerun Configure to review the selection. Unassigned entities must be assigned to an Area using HA's normal device/entity UI first.

Automatic candidates: non-disabled lights/media players, motion/occupancy/presence binary sensors, illuminance/temperature/humidity sensors in selected Areas. An explicit entity Area takes precedence over its device Area. Child devices inherit a parent's Area when they have none. Configuration/diagnostic entities and disabled devices/entities are excluded from defaults; unavailable/unknown entities remain selectable. Derived occupancy is not promoted to ground truth, and no PIR/mmWave role is inferred from a name. Only states and bounded light attributes are forwarded; media titles, URLs and user identities are excluded.

HA forwards state changes and ephemeral automation/script causality while a capture is active, in bounded batches. Unrelated causal records may pass through an expiring in-memory map but are not journaled. Connection/queue gaps require a fresh complete snapshot; snapshots are not actions. The worker heartbeat expires after 30 seconds and the capture timer remains enforced independently.

The integration stores its pairing key through HA's supported config-entry APIs. Masking the input does not encrypt HA backups. Do not share diagnostics containing configuration. We do not modify `.storage` directly.

This alpha provides observation, coverage reports and explicitly requested temperature forecasting experiments. It is not a device controller. Manual-override priority, nighttime/heating limits, multiple-occupant and pet uncertainty remain required before any actuation.

## Off-limits rooms and entities (0.3.1a1)

The room picker includes **Off-limits rooms** and **Off-limits entities**, separate
from reviewed observation inclusion. Exclusions override inclusion, including
declared aggregate-sensor dependencies. Selecting every reviewed entity as
off-limits pauses observation. Scope changes close the current capture and keep
its report. Registry/membership changes invalidate queued observations before
scope is reevaluated. Nothing is removed from HA or historical appdata.

The worker must advertise `explicit_exclusions` before exclusion options can be
saved. Existing installations have no exclusions by default and keep their
reviewed scope. Changing selection never automatically opts in new entities.
See [Learning engine direction](LEARNING-ENGINE.md) for how the future control
broker must enforce the same exclusions before every action.

## Extended captures and historical training

The `dwellmind.start_capture` action accepts `duration_seconds` from 10 to 86400,
using the already reviewed selection. It does not change the button's configured
default. An explicit HA automation can start successive bounded captures, with a
fixed campaign cutoff. To stop such a campaign, disable its automation before
pressing Stop; otherwise its next idle check will resume collection.

The `dwellmind.train_temperature_forecast` action accepts one reviewed
`entity_id`, an ISO `start_date`, and the home's ISO `move_date`. An optional
`entry_id` selects a worker when multiple integrations are loaded. HA reads
hourly Recorder statistics in monthly windows using its read-only helper and
converts them to the selected sensor's explicit temperature unit. Only timestamps
and means are sent to Unraid through the existing authenticated LAN connection.
No HA credentials or database files are transferred.

Unraid fits ridge autoregression candidates using all history, the current-home
period, and the recent 90 days. The penultimate 30 days select the candidate; the
final 30 days test it against persistence and the previous-day baseline. Feature
scaling and coefficients use training data only. Missing hours and move-crossing
windows are omitted. Out-of-distribution inputs abstain with an explicitly counted
persistence fallback. **Temperature learning result** exposes coverage, candidate
selection and held-out errors; a trained model can fail to improve the baselines.

This predicts the next hourly averaged temperature. It does not yet estimate the
causal effect of heating, generate lighting preference labels or control devices.
Hourly statistics cannot reconstruct historical occupancy transitions or manual
light corrections. Those tasks require the appropriate raw archive and provenance.

Each run preserves a separate owner-only source and model in `/data/learning`.
At most 45000 hourly rows, five years, 4 MiB per request and 16 training runs are
allowed. Reaching a cap refuses another run; it never silently deletes history.
Shadow campaign defaults use bounded 1 GiB memory / 2 CPU limits. Existing saved Unraid templates need their limits updated explicitly. The historical CLI
`thermal_forecast.py --input /private/history.json --output /private/model.json`
uses the same algorithm without network access.

## Report access and upgrades

The summary endpoint is authenticated and read-only. It projects approved report fields only, accepts no filename/path input and never reads raw journals or credentials. It restores the latest report by capture end time after a worker upgrade, preserving all source files.

Update both the Unraid image and HA integration to 0.2.1a1 or newer. Existing worker pairing, room selection and appdata are retained. An older worker continues to support captures; the HA summary entity shows `worker_update_required: true` until the worker image is updated. No new token is required. Stop an active capture before upgrades, then restart HA to load updated Python integration code.

The summary is private household telemetry, visible to HA users and authorized MCP clients that can read its entity. It is also eligible for normal HA Recorder storage. It contains no pairing key, raw causal context, user identity or media title. Unknown or zero-coverage devices remain represented; low coverage does not trigger cleanup or automatic exclusion.

This provides DwellMind status/report access. General Docker host administration remains a separate SSH/API permission and is not granted by pairing the worker.
