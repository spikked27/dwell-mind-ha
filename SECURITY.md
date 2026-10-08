# Security policy

DwellMind HA is an early observation-only alpha. It is not a security boundary
for a privileged host user or a compromised Python process. HA and Influx account
permissions must be restricted independently of this application's allowed commands.

## Report privately

Use GitHub's **Security → Report a vulnerability** for this repository if enabled.
Do not post real credentials, saved Unraid templates, home traces, `.env`, raw
journals or `docker inspect` output to public issues. If private vulnerability
reporting is unavailable, open a minimal issue requesting a private contact route
without exploit details or sensitive material.

## Supported versions

Security fixes target the current main/alpha release only; no stable maintenance
branch or production support promise exists yet. Keep dependency versions pinned
and review update PRs before deployment.

## Important boundaries

- Normal HA tokens may authorize device control even when DwellMind only observes.
  Prefer a dedicated non-admin user; never grant admin just for optional context.
- Masked Unraid environment fields are not encrypted secrets. File mounts reduce
  template exposure but remain readable by host/Docker administrators.
- Journals are sensitive household data. Owner-only permissions, private backups,
  local retention and trusted parent directories remain operator responsibilities.
- TLS verification stays enabled. Plain HTTP needs explicit opt-in and is not private.
- The image exposes no ports and must not receive host Docker/config/device mounts.
- Raw errors/upstream payloads are not logged. Static secret scans are defense in
  depth, not a guarantee; review source and artifacts before sharing.
- No endpoint accepts arbitrary commands or enables device control. Future control
  features require a separate design review and explicit feedback/override safeguards.

## Companion worker API

The integration uses a private worker pairing key, not a Home Assistant token. Keep the API on a trusted local network; use a local HTTPS reverse proxy where possible. HTTP requires explicit user consent. Keys are never logged and remain owner-only in appdata; HA stores them using config-entry APIs. Both host administrators and HA backup readers can access them. The API exposes no device-control or data-deletion route. Authentication, request/body limits, socket timeouts, capped storage and explicit telemetry gaps are tested. The health endpoint only confirms API responsiveness.
