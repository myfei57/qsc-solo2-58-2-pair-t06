"""A single column and its declared sensitivity."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum


class Sensitivity(str, Enum):
    PUBLIC = "public"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    SPECIAL = "special"

    @property
    def requires_masking(self) -> bool:
        return self is not Sensitivity.PUBLIC


@dataclass(frozen=True)
class Field:
    name: str
    sensitivity: str
    declared_by: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("field name is required")
        if self.sensitivity not in {item.value for item in Sensitivity}:
            raise ValueError(f"unknown sensitivity {self.sensitivity!r}")

    @property
    def sensitivity_enum(self) -> Sensitivity:
        return Sensitivity(self.sensitivity)

    def as_dict(self) -> dict[str, str]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: object) -> "Field | None":
        if not isinstance(payload, dict):
            return None
        name = payload.get("name")
        sensitivity = payload.get("sensitivity")
        declared_by = payload.get("declared_by", "")
        if not isinstance(name, str) or not isinstance(sensitivity, str):
            return None
        if not isinstance(declared_by, str):
            return None
        try:
            return cls(name=name, sensitivity=sensitivity, declared_by=declared_by)
        except ValueError:
            return None
