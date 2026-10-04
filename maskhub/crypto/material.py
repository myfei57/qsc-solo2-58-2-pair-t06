"""Cryptographic key material with an explicit lifecycle.

A :class:`Material` bundles the secret *key* and the deterministic *salt* that
used to drift between batches. They are versioned together: every ciphertext
remembers which material produced it, so rotating in new material never makes
old data (or an exported copy) undecryptable.
"""

from __future__ import annotations

import dataclasses
import hashlib
import secrets
from dataclasses import dataclass
from enum import Enum

SUPPORTED_PURPOSES = ("hash", "tokenize")
DEFAULT_DERIVE_ITERATIONS = 200_000


class MaterialState(str, Enum):
    DRAFT = "draft"                      # registered, not yet used
    ACTIVE = "active"                    # ring preferred: new writes land here
    RETIRING = "retiring"                # read/verify only, no new writes
    RETIRED = "retired"                  # still retained, not reachable in a ring
    PENDING_DESTRUCTION = "pending_destruction"
    DESTROYED = "destroyed"              # secret bytes gone, tombstone remains

    @property
    def can_read(self) -> bool:
        """Old data produced by this material can still be interpreted."""
        return self in (
            MaterialState.ACTIVE,
            MaterialState.RETIRING,
            MaterialState.RETIRED,
        )

    @property
    def can_write(self) -> bool:
        return self is MaterialState.ACTIVE


# States a material may legally move between. Every transition that fails
# validation leaves the material exactly where it was — there is no state from
# which the ring has no serving material. DESTROYED is deliberately absent:
# the secret bytes are only zeroed through ``destroyed_tombstone``.
_TRANSITIONS: dict[MaterialState, frozenset[MaterialState]] = {
    MaterialState.DRAFT: frozenset(
        {MaterialState.ACTIVE, MaterialState.PENDING_DESTRUCTION}
    ),
    MaterialState.ACTIVE: frozenset({MaterialState.RETIRING}),
    MaterialState.RETIRING: frozenset(
        {MaterialState.RETIRED, MaterialState.ACTIVE}
    ),
    MaterialState.RETIRED: frozenset({MaterialState.PENDING_DESTRUCTION}),
    MaterialState.PENDING_DESTRUCTION: frozenset({MaterialState.RETIRED}),
    MaterialState.DESTROYED: frozenset(),
}


def can_transition(current: MaterialState, target: MaterialState) -> bool:
    return target in _TRANSITIONS[current]


def generate_material(purpose: str) -> "Material":
    """Create fresh random key+salt for a purpose."""
    if purpose not in SUPPORTED_PURPOSES:
        raise ValueError(f"unsupported purpose {purpose!r}")
    return Material(
        identifier=f"{purpose}-{secrets.token_hex(6)}",
        purpose=purpose,
        key_material=secrets.token_bytes(32).hex(),
        salt=secrets.token_bytes(16).hex(),
        state=MaterialState.DRAFT.value,
    )


@dataclass(frozen=True)
class Material:
    identifier: str
    purpose: str
    key_material: str          # hex; cleared (not deleted) on destruction
    salt: str                  # hex; bound to the key for its whole lifetime
    state: str = MaterialState.DRAFT.value
    valid_from: str = ""       # ISO timestamps bound the serving window
    valid_until: str = ""
    iterations: int = DEFAULT_DERIVE_ITERATIONS
    created_at: str = ""
    fingerprint: str = ""      # SHA-256 of key+salt, retained after destruction
    destroyed_at: str = ""

    def __post_init__(self) -> None:
        if not self.identifier.strip():
            raise ValueError("material identifier is required")
        if self.purpose not in SUPPORTED_PURPOSES:
            raise ValueError(f"unsupported purpose {self.purpose!r}")
        if self.state not in {item.value for item in MaterialState}:
            raise ValueError(f"unknown material state {self.state!r}")
        try:
            bytes.fromhex(self.salt)
        except ValueError:
            raise ValueError("salt must be hex")
        # Destroyed tombstones carry no secret; everything else needs the key.
        if self.state_enum is not MaterialState.DESTROYED:
            try:
                key = bytes.fromhex(self.key_material)
            except ValueError:
                raise ValueError("key material must be hex")
            if len(key) < 16:
                raise ValueError("key material is too short")
        if self.iterations <= 0:
            raise ValueError("iterations must be positive")

    @property
    def state_enum(self) -> MaterialState:
        return MaterialState(self.state)

    @property
    def key_bytes(self) -> bytes:
        if self.state_enum is MaterialState.DESTROYED:
            raise ValueError(f"material {self.identifier} is destroyed")
        return bytes.fromhex(self.key_material)

    @property
    def salt_bytes(self) -> bytes:
        return bytes.fromhex(self.salt)

    def expected_fingerprint(self) -> str:
        return hashlib.sha256(
            self.key_material.encode() + b"|" + self.salt.encode()
        ).hexdigest()

    def with_state(self, state: MaterialState) -> "Material":
        if not can_transition(self.state_enum, state):
            raise ValueError(
                f"material {self.identifier} cannot move "
                f"{self.state} -> {state.value}"
            )
        return dataclasses.replace(self, state=state.value)

    def destroyed_tombstone(self, destroyed_at: str) -> "Material":
        """Return the DESTROYED view: secret zeroed, proof retained."""
        if self.state_enum is not MaterialState.PENDING_DESTRUCTION:
            raise ValueError("only pending material can be destroyed")
        return dataclasses.replace(
            self,
            state=MaterialState.DESTROYED.value,
            key_material="",
            destroyed_at=destroyed_at,
        )

    def as_dict(self) -> dict[str, object]:
        document: dict[str, object] = {
            "identifier": self.identifier,
            "purpose": self.purpose,
            "key_material": self.key_material,
            "salt": self.salt,
            "state": self.state,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "iterations": self.iterations,
            "created_at": self.created_at,
            "fingerprint": self.fingerprint,
            "destroyed_at": self.destroyed_at,
        }
        return document

    @classmethod
    def from_dict(cls, payload: object) -> "Material | None":
        if not isinstance(payload, dict):
            return None
        try:
            return cls(
                identifier=str(payload["identifier"]),
                purpose=str(payload["purpose"]),
                key_material=str(payload.get("key_material", "")),
                salt=str(payload["salt"]),
                state=str(payload.get("state", MaterialState.DRAFT.value)),
                valid_from=str(payload.get("valid_from", "")),
                valid_until=str(payload.get("valid_until", "")),
                iterations=int(payload.get("iterations", DEFAULT_DERIVE_ITERATIONS)),
                created_at=str(payload.get("created_at", "")),
                fingerprint=str(payload.get("fingerprint", "")),
                destroyed_at=str(payload.get("destroyed_at", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None
