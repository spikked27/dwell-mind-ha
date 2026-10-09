# DwellMind HA

Local room observation and experimental learning for Home Assistant. Home Assistant is the configuration interface; Unraid is the workhorse. No add-on, cloud inference, GPU or LLM is required.

## Install

- [Unraid worker and template](docs/UNRAID.md)
- [Home Assistant integration: pair, choose rooms, review entities](docs/INTEGRATION.md)

The native HA setup flow offers Area and entity pickers. Room/device membership is discovered through supported registries. Docker infrastructure settings contain no household entity IDs or HA access token. Start a bounded capture with HA's button; five minutes is the default. The worker remains idle between jobs.

## Current capabilities

- Selected state-change observation, attribution and explicit availability/transport gaps.
- Native HA configuration/options flow, status sensors, start/stop buttons.
- Authenticated bounded worker API, private journals and immutable capture selection.
- No device control, automatic preference labels or historical-data cleanup.
- Offline chronological baseline analysis retained from the initial Office/Living Room pilots.

**This is an observation alpha.** The full preference/thermal learner and controller are future work. Existing automation actions and learner actions must never become preference labels. Quick reversals remain evidence with uncertainty. Occupants and pets cannot be reliably distinguished by occupancy sensors alone.

## Resource and privacy bounds

Unraid template: 1 GiB memory/no extra swap, 2 CPUs, 32 processes, bounded logs, read-only root filesystem, minimal startup capabilities and non-root network processing. Each capture has bounded journal storage and a 64-run cap; no old data is deleted. Worker API access is authenticated and must stay on a trusted LAN or behind a local HTTPS reverse proxy. Direct HTTP requires explicit consent during HA pairing.

CI validates worker logic, pinned HA integration APIs, container build and offline smoke tests before GHCR publishing. Actual HA/Unraid deployment remains a separate verification step.

See [testing](docs/TESTING.md), [architecture](docs/ARCHITECTURE.md), [historical analysis](docs/HISTORICAL.md), [roadmap](ROADMAP.md), [security](SECURITY.md), and [license](LICENSE).

[Shadow campaign capabilities, evaluation and limits](docs/SHADOW-CAMPAIGN.md).
