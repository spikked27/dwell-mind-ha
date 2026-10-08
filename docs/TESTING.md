# Testing, releases and honest readiness

## Local verification

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: --require-hashes -r requirements-live.txt
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_release.py
```

The default tests use the OS temporary directory. Set `DWELLMIND_TEST_TMPDIR` to
an existing writable private directory if your harness requires another location.
No Home Assistant credentials or live home connection are required.

Test layers:
- Policy/query security, strict parsing, result projection, timestamp precision.
- Historical evidence reconstruction, chronology, actor classification, gaps.
- Compressed WebSocket snapshots/diffs, attribute/context removal, bounds.
- Mocked live capture/reconnect/auth/permission/heartbeat state machine.
- Real pinned WebSocket library against a **loopback dummy HA** endpoint. Tests
  explicitly skip only if the optional dependency is absent; CI installs it.
- Container environment, custom room profiles, masked Unraid fields, permissions,
  capture-to-summary integration and exclusive journal output.
- Static public-export checks for known credential shapes/private paths.

These are not proof of real HA permissions, Unraid UI rendering, physical sensor
accuracy, TLS trust, reverse-proxy compatibility, or production security. The frame
adapter intentionally depends on a reviewed private API of websocket-client 1.9.2.
Review the code, pin, wheel hash and tests together when updating it.

## Container verification

On a Docker host:

```sh
docker build -t dwellmind-ha:test .
bash scripts/container_smoke.sh
```

The smoke check uses a temporary Docker volume, no network, and a dummy token.
It verifies entrypoint/configuration, UID/mode ownership and missing-token refusal.
It does not connect to HA or control devices. CI runs this before publishing.

## GitHub Actions

- **Tests and container smoke check:** push, PR and manual runs; Python 3.13/3.14,
  unit/loopback tests and Docker startup checks.
- **Publish container:** main, `v*` tags or manual runs; re-runs tests and smoke
  checks, then builds amd64/arm64 images and pushes to GHCR.
- Actions and base image are commit/digest pinned. Dependabot proposes updates;
  no automatic merges or credential-bearing PR workflows.
- The image job alone receives `packages: write`; the automatic repository
  `GITHUB_TOKEN` is used. Do not add a HA token or personal home secret to Actions.
- The public image embeds source/license labels and build provenance/SBOM.

Only actual successful workflow results establish image build/publish success.
After first publish, verify package visibility is public and anonymous pull works.
Tags: `edge` for main, `sha-<commit>` and release ref tags such as `v0.1.0-alpha.1`.
There is no automatically promoted stable/latest image in this alpha.

## Release checklist

1. Ensure source/tests/docs contain no household data or credentials.
2. Verify local tests, loopback tests and static export check.
3. Require successful GitHub container smoke and multiarchitecture build.
4. Verify anonymous GHCR pull and template fields in Unraid.
5. Run a five-minute capture locally with reviewed HA permissions.
6. Inspect initialized entity count, attribution limitations, gaps and private summary.
7. Document real HA/Unraid versions tested; do not claim untested compatibility.
8. Tag an alpha release only after recording results; retain observation-only status.

## Companion service verification

Worker unit tests cover pairing/authentication, area inheritance, unsupported entity filtering, idempotency, snapshots, sequence/heartbeat gaps, negative numeric readings, retention preservation, disk refusal and causal projection. Container smoke also runs the persistent API without external networking, performs authenticated observation, checks its private report and stops gracefully.

The HA companion CI job installs pinned `homeassistant==2026.9.4` under Python 3.14 and runs `python -m unittest discover -s integration_tests -v`. It exercises real selector/flow APIs with controlled registries and a mocked worker client. It is not a full live-HA UI or HACS installation test.
