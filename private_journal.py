"""Exclusive owner-only, bounded journal. Never logs raw events or secrets."""
import json
import os
from pathlib import Path
import stat

from policy import SafeError


class Journal:
    def __init__(self, directory, prefix, max_file_bytes=4194304, max_files=4):
        if not prefix or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789-_" for c in prefix):
            raise SafeError("Invalid journal prefix.")
        if type(max_files) is not int or not 1 <= max_files <= 4 or type(max_file_bytes) is not int or not 1024 <= max_file_bytes <= 16777216:
            raise SafeError("Invalid journal bounds.")
        self.directory = Path(directory)
        # O_DIRECTORY/O_NOFOLLOW protects the final component; parents must be owner-trusted.
        self.directory.mkdir(mode=0o700, exist_ok=True)
        self.dirfd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        info = os.fstat(self.dirfd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            os.close(self.dirfd)
            raise SafeError("Journal directory must be owner-only and service-owned.")
        self.prefix, self.max_file_bytes, self.max_files = prefix, max_file_bytes, max_files
        self.index, self.size, self.handle, self.rows = 0, 0, None, 0

    def _open(self):
        if self.index >= self.max_files:
            raise SafeError("Journal disk budget reached; capture stopped.")
        self.index += 1
        name = f"{self.prefix}-{self.index:02d}.jsonl"
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.dirfd)
        self.handle, self.size = os.fdopen(fd, "wb"), 0

    def write(self, row):
        raw = (json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n").encode()
        if len(raw) > min(self.max_file_bytes, 16384):
            raise SafeError("Projected journal row exceeds byte budget.")
        if self.handle is None:
            self._open()
        if self.size + len(raw) > self.max_file_bytes:
            self.handle.close()
            self.handle = None
            self._open()
        self.handle.write(raw)
        self.handle.flush()
        self.size += len(raw)
        self.rows += 1

    def close(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None
        if self.dirfd is not None:
            os.close(self.dirfd)
            self.dirfd = None
