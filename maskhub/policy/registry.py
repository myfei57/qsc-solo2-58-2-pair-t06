"""Policy storage with a single-serving-state rule."""

from __future__ import annotations

from maskhub.errors import Conflict, NotFound
from maskhub.policy.policy import Policy, PolicyState
from maskhub.store.kvstore import RecordStore

PREFIX = "policy."


def policy_key(identifier: str) -> str:
    return PREFIX + identifier


class PolicyRegistry:
    def __init__(self, store: RecordStore) -> None:
        self._store = store

    def save(self, policy: Policy) -> Policy:
        existing = self.find(policy.identifier)
        if existing is not None and existing.state_enum is PolicyState.ACTIVE:
            raise Conflict(f"policy {policy.identifier} is active and cannot be edited")
        self._store.put_json(policy_key(policy.identifier), policy.as_dict())
        return policy

    def find(self, identifier: str) -> Policy | None:
        return Policy.from_dict(self._store.get_json(policy_key(identifier)))

    def get(self, identifier: str) -> Policy:
        policy = self.find(identifier)
        if policy is None:
            raise NotFound(f"unknown policy {identifier}")
        return policy

    def activate(self, identifier: str) -> Policy:
        policy = self.get(identifier)
        for other in self.all():
            if other.identifier == identifier:
                continue
            if other.state_enum is not PolicyState.ACTIVE:
                continue
            if set(other.datasets) & set(policy.datasets):
                raise Conflict(
                    f"policy {other.identifier} already serves {sorted(set(other.datasets) & set(policy.datasets))}"
                )
        activated = policy.with_state(PolicyState.ACTIVE)
        self._store.put_json(policy_key(identifier), activated.as_dict())
        return activated

    def retire(self, identifier: str) -> Policy:
        policy = self.get(identifier)
        retired = policy.with_state(PolicyState.RETIRED)
        self._store.put_json(policy_key(identifier), retired.as_dict())
        return retired

    def identifiers(self) -> list[str]:
        from maskhub.store.query import keys_with_prefix

        return [key[len(PREFIX):] for key in keys_with_prefix(self._store, PREFIX)]

    def all(self) -> list[Policy]:
        return [self.get(identifier) for identifier in self.identifiers()]

    def serving(self) -> list[Policy]:
        return [policy for policy in self.all() if policy.state_enum.is_serving]
