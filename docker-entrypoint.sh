#!/bin/sh
set -eu
# Never enable shell tracing: HA_TOKEN may be supplied by Unraid's template.
# root is used ONLY to initialize an empty Docker-created appdata mount, then
# irreversibly drop to the configured non-root identity before Python/network IO.
if [ "$(id -u)" = "0" ]; then
    exec python3 /app/drop_privileges.py "$@"
fi
exec python3 /app/container_app.py "$@"
