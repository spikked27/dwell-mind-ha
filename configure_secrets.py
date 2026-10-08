"""Interactive local utility. Never run through the agent or upload its output files."""
import argparse
import getpass
import os
from pathlib import Path
import re
import secrets
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", default="./private")
    parser.add_argument("--uid", type=int, default=10001)
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise SystemExit("Use an interactive local terminal, not agent execution or pipes.")
    if args.uid < 1 or (args.uid != os.geteuid() and os.geteuid() != 0):
        raise SystemExit("Use a non-root UID; run locally as root to assign container ownership.")
    directory = Path(args.directory)
    if directory.exists() or directory.is_symlink():
        raise SystemExit("Refusing to overwrite an existing directory.")
    username = getpass.getpass("Read-only InfluxDB username (hidden): ")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", username):
        raise SystemExit("Use an alphanumeric/underscore/hyphen username.")
    password = getpass.getpass("InfluxDB password (hidden): ")
    confirmation = getpass.getpass("Confirm password (hidden): ")
    if password != confirmation or not 16 <= len(password) <= 1024 or any(ord(c) < 32 for c in password):
        raise SystemExit("Passwords must match and contain 16–1024 printable characters.")
    os.umask(0o077)
    directory.mkdir(mode=0o700)
    for name, value in [("influx_username", username), ("influx_password", password),
                        ("mcp_token", secrets.token_urlsafe(48))]:
        fd = os.open(directory / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(value + "\n")
        if os.geteuid() == 0:
            os.chown(directory / name, args.uid, args.uid)
    if os.geteuid() == 0:
        os.chown(directory, args.uid, args.uid)
    print("Owner-only secret files created. No secret values were printed.")
    print("Keep this directory on the connector host, outside agent-accessible mounts.")


if __name__ == "__main__":
    main()
