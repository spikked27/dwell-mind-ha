"""Root-only mount initializer; never chowns existing content recursively."""
import os
from pathlib import Path
import stat
import sys


def main():
    uid, gid = int(os.environ.get('PUID','99')), int(os.environ.get('PGID','100'))
    if not 1 <= uid <= 2147483647 or not 1 <= gid <= 2147483647:
        raise ValueError('Invalid non-root identity')
    path = Path(os.environ.get('DATA_DIR','/data'))
    if not path.is_absolute():
        raise ValueError('Absolute data directory required')
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError('Data directory must be a real directory')
    # Restricted root has no DAC_OVERRIDE and cannot open a prepared 0700
    # directory belonging to the capture UID. Leave it intact and drop identity.
    if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
        fd = os.open(path,os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info=os.fstat(fd)
            if info.st_uid != uid or stat.S_IMODE(info.st_mode) != 0o700:
                if os.listdir(fd):
                    raise ValueError('Existing appdata needs owner-only permissions for the selected UID')
                os.fchown(fd,uid,gid)
                os.fchmod(fd,0o700)
        finally:
            os.close(fd)
    os.setgroups([])
    os.setgid(gid)
    os.setuid(uid)
    os.umask(0o077)
    os.execv(sys.executable,[sys.executable,'/app/container_app.py',*sys.argv[1:]])


if __name__=='__main__':
    try:
        main()
    except Exception:
        raise SystemExit('Appdata initialization refused. Use an empty directory or set existing owner/mode to PUID:PGID and 0700 locally. No files recursively modified.') from None
