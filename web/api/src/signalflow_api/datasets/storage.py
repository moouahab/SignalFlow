"""Stockage privé plat ; création exclusive, sans suivre les liens symboliques."""
import hashlib
import os
from pathlib import Path
import re
import stat
import uuid

from ..errors import ApiError


class Storage:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Refuser également les liens dans les ancêtres configurés.
        if any(p.is_symlink() for p in (self.root, *self.root.parents)):
            raise OSError('unsafe storage root')

    def path(self, key):
        if not re.fullmatch(r'[a-f0-9]{32}', key):
            raise ValueError('invalid storage key')
        return self.root / key

    def stage(self, source, limit):
        key = uuid.uuid4().hex
        path = self.path(key)
        size, digest = 0, hashlib.sha256()
        created = False
        try:
            with path.open('xb') as output:
                created = True
                os.chmod(path, 0o600)
                while chunk := source.read(65536):
                    size += len(chunk)
                    if size > limit:
                        raise ApiError(413, 'DATASET_TOO_LARGE', 'Dataset trop volumineux.')
                    digest.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            directory = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            return key, size, digest.hexdigest()
        except BaseException:
            if created:
                path.unlink(missing_ok=True)
            raise

    def open(self, key):
        fd = os.open(self.path(key), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise OSError('not a regular file')
        return os.fdopen(fd, 'rb')

    def discard(self, key):
        self.path(key).unlink(missing_ok=True)
