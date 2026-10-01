"""Thread-safe JSON document store backed by one file per key."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

SUFFIX = ".json"
FORBIDDEN = ("/", "\\", "..", "\x00")


class StoreKeyError(KeyError):
    """Raised when a key cannot be used as a file name."""


def encode(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False).encode("utf-8")


def decode(raw: bytes) -> Any:
    return json.loads(raw.decode("utf-8"))


def validate_key(key: str) -> str:
    trimmed = key.strip()
    if not trimmed:
        raise StoreKeyError("store key is empty")
    for fragment in FORBIDDEN:
        if fragment in trimmed:
            raise StoreKeyError(f"store key contains {fragment!r}")
    return trimmed


class RecordStore:
    def __init__(self, directory: str | os.PathLike[str]) -> None:
        self._dir = Path(directory)
        self._data: dict[str, bytes] = {}
        self._lock = threading.RLock()

    def load(self) -> None:
        self._dir.mkdir(parents=True, exist_ok=True)
        with self._lock:
            self._data.clear()
            for path in sorted(self._dir.glob(f"*{SUFFIX}")):
                if path.is_file():
                    self._data[path.name[: -len(SUFFIX)]] = path.read_bytes()

    def put_json(self, key: str, value: Any) -> None:
        self.put(key, encode(value))

    def put(self, key: str, payload: bytes) -> None:
        name = validate_key(key)
        with self._lock:
            self._write(name, payload)

    def get_json(self, key: str) -> Any | None:
        raw = self.get(key)
        if raw is None:
            return None
        try:
            return decode(raw)
        except ValueError:
            return None

    def get(self, key: str) -> bytes | None:
        with self._lock:
            return self._data.get(key)

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self._data

    def delete(self, key: str) -> None:
        name = validate_key(key)
        with self._lock:
            self._data.pop(name, None)
            try:
                self._path_for(name).unlink()
            except FileNotFoundError:
                pass

    def update_json(self, key: str, mutator: Callable[[Any], tuple[Any, Any]]) -> Any:
        name = validate_key(key)
        with self._lock:
            current = None
            raw = self._data.get(name)
            if raw is not None:
                try:
                    current = decode(raw)
                except ValueError:
                    current = None
            new_document, result = mutator(current)
            if new_document is not None:
                self._write(name, encode(new_document))
            return result

    @contextmanager
    def transaction(self) -> Iterator[dict[str, bytes]]:
        with self._lock:
            yield self._data

    def _path_for(self, key: str) -> Path:
        return self._dir / f"{key}{SUFFIX}"

    def _write(self, key: str, payload: bytes) -> None:
        path = self._path_for(key)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_bytes(payload)
        os.replace(tmp, path)
        self._data[key] = payload
