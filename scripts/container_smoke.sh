#!/usr/bin/env bash
set -euo pipefail
# No real tokens or HA network connection. CI owns these temporary resources.
volume="dwellmind-smoke-${RANDOM}-${RANDOM}"
docker volume create "$volume" >/dev/null
trap 'docker volume rm "$volume" >/dev/null' EXIT
flags=(--rm --network=none --read-only --cap-drop=ALL --cap-add=CHOWN --cap-add=FOWNER --cap-add=SETUID --cap-add=SETGID --security-opt=no-new-privileges:true --pids-limit=32 --memory=128m)
docker run "${flags[@]}" -v "$volume:/data" dwellmind-ha:test --version
docker run "${flags[@]}" -v "$volume:/data" -e HA_URL=https://ha.example.invalid -e HA_TOKEN=dummy-ci-token-not-real-0000000000000000 dwellmind-ha:test --check
docker run "${flags[@]}" --user 99:100 --entrypoint python3 -v "$volume:/data" dwellmind-ha:test -c 'import os,stat; assert os.geteuid()==99; assert stat.S_IMODE(os.stat("/data").st_mode)==0o700; assert os.stat("/data").st_uid==99'
if docker run "${flags[@]}" -v "$volume:/data" -e HA_URL=https://ha.example.invalid dwellmind-ha:test --check; then
    echo 'Missing token must fail configuration validation' >&2
    exit 1
fi
