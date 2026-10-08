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
7. Review worker status, selected rooms/entities, observation rows and capture gaps. Detailed reports remain private on Unraid.

Use the integration's **Configure** action to change rooms/entities. Stop an active capture first. No deletion or reassignment of existing HA entities occurs. Newly assigned entities are not silently subscribed: rerun Configure to review the selection. Unassigned entities must be assigned to an Area using HA's normal device/entity UI first.

Automatic candidates: non-disabled lights/media players, motion/occupancy/presence binary sensors, illuminance/temperature/humidity sensors in selected Areas. An explicit entity Area takes precedence over its device Area. Child devices inherit a parent's Area when they have none. Configuration/diagnostic entities and disabled devices/entities are excluded from defaults; unavailable/unknown entities remain selectable. Derived occupancy is not promoted to ground truth, and no PIR/mmWave role is inferred from a name. Only states and bounded light attributes are forwarded; media titles, URLs and user identities are excluded.

HA forwards state changes and ephemeral automation/script causality while a capture is active, in bounded batches. Unrelated causal records may pass through an expiring in-memory map but are not journaled. Connection/queue gaps require a fresh complete snapshot; snapshots are not actions. The worker heartbeat expires after 30 seconds and the capture timer remains enforced independently.

The integration stores its pairing key through HA's supported config-entry APIs. Masking the input does not encrypt HA backups. Do not share diagnostics containing configuration. We do not modify `.storage` directly.

This alpha provides observation and coverage reports, not a continuous training scheduler or controller. The planned learning service must retain baselines/held-out evaluation, manual-override priority, nighttime/heating limits, multiple-occupant and pet uncertainty before any actuation.
