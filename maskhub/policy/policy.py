"""A masking policy binding datasets to rules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class PolicyState(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    RETIRED = "retired"

    @property
    def is_serving(self) -> bool:
        return self is PolicyState.ACTIVE


@dataclass(frozen=True)
class Policy:
    identifier: str
    datasets: tuple[str, ...]
    rules: tuple[str, ...]
    state: str = PolicyState.DRAFT.value

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise ValueError("policy identifier is required")
        if not self.datasets:
            raise ValueError("policy must target at least one dataset")
        if not self.rules:
            raise ValueError("policy must reference at least one rule")
        if self.state not in {item.value for item in PolicyState}:
            raise ValueError(f"unknown policy state {self.state!r}")

    @property
    def state_enum(self) -> PolicyState:
        return PolicyState(self.state)

    def with_state(self, state: PolicyState) -> "Policy":
        return Policy(
            identifier=self.identifier,
            datasets=self.datasets,
            rules=self.rules,
            state=state.value,
        )

    def covers(self, dataset: str) -> bool:
        return dataset in self.datasets

    def as_dict(self) -> dict[str, object]:
        return {
            "identifier": self.identifier,
            "datasets": list(self.datasets),
            "rules": list(self.rules),
            "state": self.state,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "Policy | None":
        if not isinstance(payload, dict):
            return None
        identifier = payload.get("identifier")
        datasets = payload.get("datasets")
        rules = payload.get("rules")
        state = payload.get("state", PolicyState.DRAFT.value)
        if not isinstance(identifier, str) or not isinstance(state, str):
            return None
        if not isinstance(datasets, list) or not isinstance(rules, list):
            return None
        try:
            return cls(
                identifier=identifier,
                datasets=tuple(str(item) for item in datasets),
                rules=tuple(str(item) for item in rules),
                state=state,
            )
        except ValueError:
            return None
