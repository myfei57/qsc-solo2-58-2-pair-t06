"""Key material states, effective intervals, and the per-purpose ring."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class MaterialState(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"
    DESTROYED = "destroyed"

    @property
    def serves_writes(self) -> bool:
        """Only the active material may mask new data."""
        return self is MaterialState.ACTIVE

    @property
    def serves_reads(self) -> bool:
        """Active and retired material can still explain existing data."""
        return self in (MaterialState.ACTIVE, MaterialState.RETIRED)


@dataclass(frozen=True)
class KeyMaterial:
    identifier: str
    purpose: str
    secret: str | None
    state: str = MaterialState.DRAFT.value
    created_at: str = ""
    activated_at: str | None = None
    retired_at: str | None = None
    destroyed_at: str | None = None
    leases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise ValueError("material identifier is required")
        if not self.purpose.strip():
            raise ValueError("material purpose is required")
        if self.state not in {item.value for item in MaterialState}:
            raise ValueError(f"unknown material state {self.state!r}")

    @property
    def state_enum(self) -> MaterialState:
        return MaterialState(self.state)

    @property
    def effective_interval(self) -> tuple[str | None, str | None]:
        """The window during which new data was masked with this material."""
        return self.activated_at, self.retired_at

    def activated(self, at: str) -> "KeyMaterial":
        return replace(self, state=MaterialState.ACTIVE.value, activated_at=at)

    def retired(self, at: str) -> "KeyMaterial":
        return replace(self, state=MaterialState.RETIRED.value, retired_at=at)

    def destroyed(self, at: str) -> "KeyMaterial":
        return replace(self, state=MaterialState.DESTROYED.value, secret=None, destroyed_at=at)

    def with_lease(self, dataset: str) -> "KeyMaterial":
        if dataset in self.leases:
            return self
        return replace(self, leases=tuple(sorted((*self.leases, dataset))))

    def without_lease(self, dataset: str) -> "KeyMaterial":
        return replace(self, leases=tuple(item for item in self.leases if item != dataset))

    def as_dict(self) -> dict[str, object]:
        """Full storage form, including the secret."""

        return {
            "identifier": self.identifier,
            "purpose": self.purpose,
            "secret": self.secret,
            "state": self.state,
            "created_at": self.created_at,
            "activated_at": self.activated_at,
            "retired_at": self.retired_at,
            "destroyed_at": self.destroyed_at,
            "leases": list(self.leases),
        }

    def public_dict(self) -> dict[str, object]:
        """API form: the stored secret is never echoed back."""

        payload = self.as_dict()
        payload.pop("secret")
        payload["has_secret"] = self.secret is not None
        return payload

    @classmethod
    def from_dict(cls, payload: object) -> "KeyMaterial | None":
        if not isinstance(payload, dict):
            return None
        identifier = payload.get("identifier")
        purpose = payload.get("purpose")
        secret = payload.get("secret")
        state = payload.get("state", MaterialState.DRAFT.value)
        created_at = payload.get("created_at", "")
        leases = payload.get("leases", [])
        if not isinstance(identifier, str) or not isinstance(purpose, str):
            return None
        if secret is not None and not isinstance(secret, str):
            return None
        if not isinstance(state, str) or not isinstance(created_at, str):
            return None
        if not isinstance(leases, list) or not all(isinstance(item, str) for item in leases):
            return None
        stamps: dict[str, str | None] = {}
        for key in ("activated_at", "retired_at", "destroyed_at"):
            value = payload.get(key)
            if value is not None and not isinstance(value, str):
                return None
            stamps[key] = value
        try:
            return cls(
                identifier=identifier,
                purpose=purpose,
                secret=secret,
                state=state,
                created_at=created_at,
                leases=tuple(leases),
                **stamps,
            )
        except ValueError:
            return None


@dataclass(frozen=True)
class Rotation:
    """A prepared cutover: ``target`` becomes the write material on commit."""

    target: str
    began_at: str

    def as_dict(self) -> dict[str, str]:
        return {"target": self.target, "began_at": self.began_at}

    @classmethod
    def from_dict(cls, payload: object) -> "Rotation | None":
        if not isinstance(payload, dict):
            return None
        target = payload.get("target")
        began_at = payload.get("began_at")
        if not isinstance(target, str) or not isinstance(began_at, str):
            return None
        return cls(target=target, began_at=began_at)


@dataclass(frozen=True)
class KeyRing:
    """All material for one purpose plus the single write pointer.

    Every lifecycle transition rewrites this one document atomically, so a
    cutover never leaves readers with two active materials or with none.
    """

    purpose: str
    write_material: str | None = None
    materials: tuple[KeyMaterial, ...] = ()
    rotation: Rotation | None = None

    def material(self, identifier: str) -> KeyMaterial | None:
        for item in self.materials:
            if item.identifier == identifier:
                return item
        return None

    def active(self) -> KeyMaterial | None:
        if self.write_material is None:
            return None
        return self.material(self.write_material)

    def with_material(self, material: KeyMaterial) -> "KeyRing":
        kept = [item for item in self.materials if item.identifier != material.identifier]
        kept.append(material)
        return replace(self, materials=tuple(kept))

    def without_material(self, identifier: str) -> "KeyRing":
        return replace(
            self,
            materials=tuple(item for item in self.materials if item.identifier != identifier),
        )

    def with_write_material(self, identifier: str | None) -> "KeyRing":
        return replace(self, write_material=identifier)

    def with_rotation(self, rotation: Rotation | None) -> "KeyRing":
        return replace(self, rotation=rotation)

    def as_dict(self) -> dict[str, object]:
        return {
            "purpose": self.purpose,
            "write_material": self.write_material,
            "rotation": self.rotation.as_dict() if self.rotation else None,
            "materials": [item.as_dict() for item in self.materials],
        }

    @classmethod
    def from_dict(cls, payload: object) -> "KeyRing | None":
        if not isinstance(payload, dict):
            return None
        purpose = payload.get("purpose")
        write_material = payload.get("write_material")
        raw_rotation = payload.get("rotation")
        raw_materials = payload.get("materials", [])
        if not isinstance(purpose, str):
            return None
        if write_material is not None and not isinstance(write_material, str):
            return None
        if not isinstance(raw_materials, list):
            return None
        rotation = Rotation.from_dict(raw_rotation) if raw_rotation is not None else None
        if raw_rotation is not None and rotation is None:
            return None
        materials = tuple(
            item
            for item in (KeyMaterial.from_dict(raw) for raw in raw_materials)
            if item is not None
        )
        if len(materials) != len(raw_materials):
            return None
        return cls(
            purpose=purpose,
            write_material=write_material,
            materials=materials,
            rotation=rotation,
        )
