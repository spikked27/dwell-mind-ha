# Contributing

Welcome! Start with an issue describing the problem and evidence scope. Use
synthetic fixtures rather than household exports. Keep changes focused and explain
whether they affect capture, interpretation, security boundaries or deployment.

1. Fork/branch the repository and create a Python 3.13+ venv.
2. Install the pinned optional transport dependency for full tests.
3. Run `python -m unittest discover -s tests -v` and `python scripts/check_release.py`.
4. For packaging changes, run the Docker smoke check on a Docker host.
5. Update docs and CHANGELOG.md with actual behavior and limits.
6. Open a PR with test evidence. Never include tokens, real user/context IDs or
   private captures. Dependency/frame-adapter updates require compatibility tests.

Preserve these invariants:
- no device-control command path in the observer;
- explicit entity allowlists, bounded inputs/outputs/storage/retries;
- missing evidence stays unknown, not absent or manual;
- snapshots and engine actions never become preference labels;
- chronological evaluation, no future-data leakage;
- private data and raw errors never reach public artifacts;
- initial appdata setup never recursively changes existing content.

Contributions are accepted under the repository's MIT license. This project is
independent of Home Assistant and its maintainers.
