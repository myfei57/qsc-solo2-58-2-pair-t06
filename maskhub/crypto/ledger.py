"""Ledger of which in-service data depends on which material.

Destruction is gated on evidence, not on memory: every live dataset, batch or
exported copy that can only be interpreted with a material is registered as a
reference here. A material may be destroyed only after every reference has
been released (data migrated, or the exported copy's retention acknowledged).

References are keyed deterministically from ``(kind, scope, name)`` so that
re-registering the same artifact is idempotent rather than double counted.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from maskhub.errors import Conflict, NotFound
from maskhub.store.kvstore import RecordStore

PREFIX = "crypto.use."

REFERENCE_KINDS = ("dataset", "batch", "export")
REFERENCE_STATES = ("active", "released")


def reference_id(kind: str, scope: str, name: str) -> str:
    if kind not in REFERENCE_KINDS:
        raise ValueError(f"unknown reference kind {kind!r}")
    if not scope.strip() or not name.strip():
        raise ValueError("reference scope and name are required")
    digest = hashlib.sha256(f"{kind}\x1f{scope}\x1f{name}".encode()).hexdigest()
    return f"{kind}-{digest[:16]}"


def reference_key(identifier: str) -> str:
    return PREFIX + identifier


@dataclass(frozen=True)
class MaterialReference:
    identifier: str
    kind: str
    scope: str                 # e.g. dataset id or export destination
    name: str                  # batch id, file name, ...
    material_identifier: str
    state: str = "active"
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise ValueError("reference identifier is required")
        if self.kind not in REFERENCE_KINDS:
            raise ValueError(f"unknown reference kind {self.kind!r}")
        if not self.material_identifier.strip():
            raise ValueError("reference must name a material")
        if self.state not in REFERENCE_STATES:
            raise ValueError(f"unknown reference state {self.state!r}")

    @property
    def is_active(self) -> bool:
        return self.state == "active"

    def released(self) -> "MaterialReference":
        return MaterialReference(
            identifier=self.identifier,
            kind=self.kind,
            scope=self.scope,
            name=self.name,
            material_identifier=self.material_identifier,
            state="released",
            detail=self.detail,
        )

    def rebound(self, material_identifier: str) -> "MaterialReference":
        """Record that this artifact was migrated onto another material."""
        return MaterialReference(
            identifier=self.identifier,
            kind=self.kind,
            scope=self.scope,
            name=self.name,
            material_identifier=material_identifier,
            state="active",
            detail=self.detail,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "identifier": self.identifier,
            "kind": self.kind,
            "scope": self.scope,
            "name": self.name,
            "material_identifier": self.material_identifier,
            "state": self.state,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "MaterialReference | None":
        if not isinstance(payload, dict):
            return None
        try:
            return cls(
                identifier=str(payload["identifier"]),
                kind=str(payload["kind"]),
                scope=str(payload["scope"]),
                name=str(payload["name"]),
                material_identifier=str(payload["material_identifier"]),
                state=str(payload.get("state", "active")),
                detail=str(payload.get("detail", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None


class DependencyLedger:
    def __init__(self, store: RecordStore) -> None:
        self._store = store

    def register(
        self,
        kind: str,
        scope: str,
        name: str,
        material_identifier: str,
        detail: str = "",
    ) -> MaterialReference:
        identifier = reference_id(kind, scope, name)
        existing = self.find(identifier)
        if existing is not None and existing.is_active:
            if existing.material_identifier != material_identifier:
                raise Conflict(
                    f"{kind} {scope}/{name} is already bound to material "
                    f"{existing.material_identifier}; migrate it explicitly"
                )
            return existing
        reference = MaterialReference(
            identifier=identifier,
            kind=kind,
            scope=scope,
            name=name,
            material_identifier=material_identifier,
            detail=detail,
        )
        self._store.put_json(reference_key(identifier), reference.as_dict())
        return reference

    def get(self, identifier: str) -> MaterialReference:
        reference = self.find(identifier)
        if reference is None:
            raise NotFound(f"unknown material reference {identifier}")
        return reference

    def find(self, identifier: str) -> MaterialReference | None:
        return MaterialReference.from_dict(
            self._store.get_json(reference_key(identifier))
        )

    def release(self, identifier: str) -> MaterialReference:
        reference = self.get(identifier)
        released = reference.released()
        self._store.put_json(reference_key(identifier), released.as_dict())
        return released

    def migrate(self, identifier: str, new_material_identifier: str) -> MaterialReference:
        """Move one artifact's dependency to another material."""
        reference = self.get(identifier)
        if not reference.is_active:
            raise Conflict(f"reference {identifier} is already released")
        migrated = reference.rebound(new_material_identifier)
        self._store.put_json(reference_key(identifier), migrated.as_dict())
        return migrated

    def references_for(self, material_identifier: str) -> list[MaterialReference]:
        return [
            reference
            for reference in self.all()
            if reference.material_identifier == material_identifier
        ]

    def active_references_for(self, material_identifier: str) -> list[MaterialReference]:
        return [
            reference
            for reference in self.references_for(material_identifier)
            if reference.is_active
        ]

    def all(self) -> list[MaterialReference]:
        from maskhub.store.query import keys_with_prefix

        return [
            reference
            for key in keys_with_prefix(self._store, PREFIX)
            if (reference := MaterialReference.from_dict(self._store.get_json(key)))
            is not None
        ]

    def proof_snapshot(self, material_identifier: str) -> dict[str, object]:
        """Evidence bundle archived at destruction time."""
        references = self.references_for(material_identifier)
        return {
            "material_identifier": material_identifier,
            "total_references": len(references),
            "active_references": sum(1 for item in references if item.is_active),
            "references": [item.as_dict() for item in references],
        }
