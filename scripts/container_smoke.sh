#!/usr/bin/env bash
set -euo pipefail
# No real tokens or HA network connection. CI owns these temporary resources.
volume="dwellmind-smoke-${RANDOM}-${RANDOM}"
docker volume create "$volume" >/dev/null
trap 'docker volume rm "$volume" >/dev/null' EXIT
flags=(--rm --network=none --read-only --cap-drop=ALL --cap-add=CHOWN --cap-add=FOWNER --cap-add=SETUID --cap-add=SETGID --security-opt=no-new-privileges:true --pids-limit=32 --memory=128m)
docker run "${flags[@]}" --mount "type=volume,src=$volume,dst=/data,volume-nocopy" dwellmind-ha:test --version
docker run "${flags[@]}" --mount "type=volume,src=$volume,dst=/data,volume-nocopy" -e HA_URL=https://ha.example.invalid -e HA_TOKEN=dummy-ci-token-not-real-0000000000000000 dwellmind-ha:test --check
docker run "${flags[@]}" --user 99:100 --entrypoint python3 --mount "type=volume,src=$volume,dst=/data,volume-nocopy" dwellmind-ha:test -c 'import os,stat; assert os.geteuid()==99; assert stat.S_IMODE(os.stat("/data").st_mode)==0o700; assert os.stat("/data").st_uid==99'
if docker run "${flags[@]}" --mount "type=volume,src=$volume,dst=/data,volume-nocopy" -e HA_URL=https://ha.example.invalid dwellmind-ha:test --check; then
    echo 'Missing token must fail configuration validation' >&2
    exit 1
fi

# The persistent worker accepts only selected observations and has no HA token.
worker="dwellmind-worker-smoke-${RANDOM}-${RANDOM}"
trap 'docker rm -f "$worker" >/dev/null 2>&1 || true; docker volume rm "$volume" >/dev/null' EXIT
docker run -d --name "$worker" --network=none --read-only --cap-drop=ALL \
    --cap-add=CHOWN --cap-add=FOWNER --cap-add=SETUID --cap-add=SETGID \
    --security-opt=no-new-privileges:true --pids-limit=32 --memory=1024m --memory-swap=1024m \
    --cpus=2.0 --mount "type=volume,src=$volume,dst=/data,volume-nocopy" dwellmind-ha:test >/dev/null
docker exec -i --user 99:100 "$worker" python3 - < scripts/service_smoke.py
docker stop --time=20 "$worker" >/dev/null
worker_exit="$(docker inspect --format='{{.State.ExitCode}}' "$worker")"
if [ "$worker_exit" != "0" ]; then
    echo "Worker graceful stop returned exit $worker_exit" >&2
    docker logs "$worker"  # Worker logs contain only fixed status/errors, never keys/payloads.
    exit 1
fi
