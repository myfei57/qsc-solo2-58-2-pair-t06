"""Append-only audit journal."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from maskhub.store.kvstore import RecordStore

JOURNAL_KEY = "audit.journal"


@dataclass(frozen=True)
class AuditEntry:
    at: str
    action: str
    subject: str
    detail: str

    def as_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: object) -> "AuditEntry | None":
        if not isinstance(payload, dict):
            return None
        at = payload.get("at")
        action = payload.get("action")
        subject = payload.get("subject")
        detail = payload.get("detail", "")
        if not all(isinstance(item, str) for item in (at, action, subject, detail)):
            return None
        return cls(at=at, action=action, subject=subject, detail=detail)


class Journal:
    def __init__(self, store: RecordStore) -> None:
        self._store = store

    def append(self, action: str, subject: str, detail: str = "") -> AuditEntry:
        entry = AuditEntry(
            at=datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            action=action,
            subject=subject,
            detail=detail,
        )

        def mutate(document: object) -> tuple[object, AuditEntry]:
            entries = _normalize(document)
            entries.append(entry)
            return [item.as_dict() for item in entries], entry

        return self._store.update_json(JOURNAL_KEY, mutate)

    def entries(self) -> list[AuditEntry]:
        return _normalize(self._store.get_json(JOURNAL_KEY))

    def by_action(self, action: str) -> list[AuditEntry]:
        return [entry for entry in self.entries() if entry.action == action]

    def latest(self) -> AuditEntry | None:
        entries = self.entries()
        return entries[-1] if entries else None

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for entry in self.entries():
            counts[entry.action] = counts.get(entry.action, 0) + 1
        return counts


def _normalize(document: object) -> list[AuditEntry]:
    if not isinstance(document, list):
        return []
    entries = [AuditEntry.from_dict(item) for item in document]
    return [entry for entry in entries if entry is not None]
