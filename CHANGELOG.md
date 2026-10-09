# Changelog

## 0.2.1a1

- Authenticated, read-only latest capture-summary API; no arbitrary file access or raw journals.
- Latest capture report HA entity, readable through existing HA MCP permissions.
- Restore report identity after worker upgrades; preserve all historical files and unknown devices.
- Backward-compatible HA integration with older capture-only workers.

## Integration 0.2.0a2

- Preserve worker URL and local HTTP consent when retrying pairing.
- Report credential-safe, specific URL, consent, network, TLS, timeout and protocol errors.
- Accept surrounding paste whitespace; never log pairing keys.
- Exercise the real aiohttp client against the worker HTTP handler.

## 0.2.0a1

- Native Home Assistant companion integration for worker pairing, Area selection, entity review, options, status and capture buttons.
- Persistent local authenticated Unraid worker; no HA token in the template.
- Bounded observation API, private selection per capture, heartbeat/sequence gaps and preserved history.
- Worker limits: 256 MiB/no extra swap, 0.5 CPU, 32 processes; liveness health check and graceful shutdown.
- Retained historical/standalone tools and observation-only behavior.

# Changelog

## 0.1.0-alpha.1 — initial public preparation

- DwellMind HA branding and public-source export without household reports/secrets.
- Configurable room entity roles, masked Unraid token input and token-file option.
- Non-root capture after narrowly scoped empty-appdata initialization.
- Bounded WebSocket observation, gap/availability markers and private summaries.
- Synthetic unit and local WebSocket integration tests.
- Docker/Compose/Unraid packaging and pinned GitHub Actions GHCR publishing.
- Historical InfluxDB sampling and exploratory baseline analysis retained as
  optional source tools; no automatic device control or production learning claim.

Image publication and real-host compatibility depend on successful CI and owner
deployment verification. Repository contents alone do not establish those results.
# 0.3.0a1

- Add bounded HA capture-duration actions for explicit extended observation campaigns.
- Add move-aware learned temperature forecasting on Unraid from hourly HA statistics.
- Compare all-history, current-home and recent-history fits using chronological validation and independent tests against persistence and previous-day baselines.
- Preserve private datasets and models; expose a bounded learning-result sensor without device control.
