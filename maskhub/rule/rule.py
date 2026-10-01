"""A rule that binds a field pattern to a masking action."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

SUPPORTED_ACTIONS = ("redact", "hash", "partial", "tokenize")


@dataclass(frozen=True)
class Rule:
    identifier: str
    field_pattern: str
    action: str
    parameters: dict[str, object]

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise ValueError("rule identifier is required")
        if not self.field_pattern.strip():
            raise ValueError("rule field pattern is required")
        if self.action not in SUPPORTED_ACTIONS:
            raise ValueError(f"unsupported action {self.action!r}")

    def matches(self, field_name: str) -> bool:
        return fnmatch.fnmatchcase(field_name.lower(), self.field_pattern.lower())

    def as_dict(self) -> dict[str, object]:
        return {
            "identifier": self.identifier,
            "field_pattern": self.field_pattern,
            "action": self.action,
            "parameters": self.parameters,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "Rule | None":
        if not isinstance(payload, dict):
            return None
        identifier = payload.get("identifier")
        pattern = payload.get("field_pattern")
        action = payload.get("action")
        parameters = payload.get("parameters", {})
        if not isinstance(identifier, str) or not isinstance(pattern, str):
            return None
        if not isinstance(action, str) or not isinstance(parameters, dict):
            return None
        try:
            return cls(
                identifier=identifier,
                field_pattern=pattern,
                action=action,
                parameters=parameters,
            )
        except ValueError:
            return None
