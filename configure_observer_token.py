"""Enter a HA token only in a private local terminal on the capture host."""
import argparse
import getpass
import os
from pathlib import Path
import re
import stat
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--file', default='./private/ha_observer_token')
    parser.add_argument('--uid', type=int, default=10001)
    args = parser.parse_args()
    if not sys.stdin.isatty() or not sys.stderr.isatty():
        raise SystemExit('Use a private interactive local terminal, not an agent or pipe.')
    if args.uid < 1 or (os.geteuid() != 0 and args.uid != os.geteuid()):
        raise SystemExit('Use a non-root service UID; run locally as root to set container ownership.')
    path = Path(args.file)
    os.umask(0o077)
    path.parent.mkdir(mode=0o700, exist_ok=True)
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(directory)
        if stat.S_IMODE(info.st_mode) & 0o077 or info.st_uid not in {os.geteuid(), args.uid}:
            raise SystemExit('Private directory must be owner-only and operator/service-owned.')
        token = getpass.getpass('HA observer token (hidden): ')
        if not re.fullmatch(r'[A-Za-z0-9._-]{32,2000}', token):
            raise SystemExit('Invalid token format; nothing saved.')
        fd = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        with os.fdopen(fd, 'w') as handle:
            handle.write(token + '\n')
            if os.geteuid() == 0:
                os.fchown(handle.fileno(), args.uid, args.uid)
        if os.geteuid() == 0:
            os.fchown(directory, args.uid, args.uid)
    finally:
        os.close(directory)
    print('Owner-only HA token file created locally; no secret printed. Existing files are never overwritten.')


if __name__ == '__main__':
    try:
        main()
    except OSError:
        raise SystemExit('Token setup refused; check local paths/ownership. No secret printed.') from None
