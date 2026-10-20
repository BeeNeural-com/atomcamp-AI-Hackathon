"""Object storage adapter: local filesystem or Google Cloud Storage.

Keys are always generated server-side (see DOCUMENT_PIPELINE.md §15).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Protocol

from beeproof.config import Settings

_SAFE_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-./]*$")


def _check_key(key: str) -> str:
    if not _SAFE_KEY.match(key) or ".." in key or key.startswith("/"):
        raise ValueError(f"unsafe storage key: {key!r}")
    return key


class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str: ...
    def get(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete_prefix(self, prefix: str) -> int: ...


class LocalStorage:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        return self.root / _check_key(key)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(path)
        return key

    def get(self, key: str) -> bytes:
        return self._path(key).read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def delete_prefix(self, prefix: str) -> int:
        base = self._path(prefix.rstrip("/"))
        if not base.exists():
            return 0
        if base.is_file():
            base.unlink()
            return 1
        n = 0
        for p in sorted(base.rglob("*"), reverse=True):
            if p.is_file():
                p.unlink()
                n += 1
            else:
                p.rmdir()
        base.rmdir()
        return n


class GCSStorage:
    def __init__(self, bucket: str, project: str | None = None):
        from google.cloud import storage  # lazy: optional dependency

        self.client = storage.Client(project=project or None)
        self.bucket = self.client.bucket(bucket)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        self.bucket.blob(_check_key(key)).upload_from_string(data, content_type=content_type)
        return key

    def get(self, key: str) -> bytes:
        return self.bucket.blob(_check_key(key)).download_as_bytes()

    def exists(self, key: str) -> bool:
        return self.bucket.blob(_check_key(key)).exists()

    def delete_prefix(self, prefix: str) -> int:
        n = 0
        for blob in self.client.list_blobs(self.bucket, prefix=_check_key(prefix)):
            blob.delete()
            n += 1
        return n


_storage: Storage | None = None


def get_storage(settings: Settings) -> Storage:
    global _storage
    if _storage is None:
        if settings.store == "gcs":
            _storage = GCSStorage(settings.bucket, settings.project)
        else:
            _storage = LocalStorage(settings.local_store_dir)
    return _storage


def reset_storage() -> None:
    global _storage
    _storage = None
