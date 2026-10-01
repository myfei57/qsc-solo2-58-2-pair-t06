"""Control-plane registry of data sources."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum

from maskhub.errors import Conflict, NotFound
from maskhub.store.kvstore import RecordStore

PREFIX = "source."


class SourceStatus(str, Enum):
    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"


@dataclass(frozen=True)
class Source:
    identifier: str
    kind: str
    endpoint: str
    status: str = SourceStatus.PENDING.value

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise ValueError("source identifier is required")
        if not self.kind.strip():
            raise ValueError("source kind is required")

    def as_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: object) -> "Source | None":
        if not isinstance(payload, dict):
            return None
        identifier = payload.get("identifier")
        kind = payload.get("kind")
        endpoint = payload.get("endpoint", "")
        status = payload.get("status", SourceStatus.PENDING.value)
        if not isinstance(identifier, str) or not isinstance(kind, str):
            return None
        if not isinstance(endpoint, str) or not isinstance(status, str):
            return None
        return cls(identifier=identifier, kind=kind, endpoint=endpoint, status=status)


def source_key(identifier: str) -> str:
    return PREFIX + identifier


class SourceRegistry:
    def __init__(self, store: RecordStore) -> None:
        self._store = store

    def register(self, source: Source) -> Source:
        if self._store.has(source_key(source.identifier)):
            raise Conflict(f"source {source.identifier} already registered")
        self._store.put_json(source_key(source.identifier), source.as_dict())
        return source

    def get(self, identifier: str) -> Source:
        source = Source.from_dict(self._store.get_json(source_key(identifier)))
        if source is None:
            raise NotFound(f"unknown source {identifier}")
        return source

    def set_status(self, identifier: str, status: SourceStatus) -> Source:
        source = self.get(identifier)
        updated = Source(
            identifier=source.identifier,
            kind=source.kind,
            endpoint=source.endpoint,
            status=status.value,
        )
        self._store.put_json(source_key(identifier), updated.as_dict())
        return updated

    def identifiers(self) -> list[str]:
        from maskhub.store.query import keys_with_prefix

        return [key[len(PREFIX):] for key in keys_with_prefix(self._store, PREFIX)]

    def all(self) -> list[Source]:
        return [self.get(identifier) for identifier in self.identifiers()]
