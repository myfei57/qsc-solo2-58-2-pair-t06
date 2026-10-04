"""Key rings: one serving material per purpose, atomic rotation.

A ring scopes material by *purpose* (hash / tokenize). Within a ring at most
one material is ACTIVE, and rotation flips the old and new states in a single
atomic commit. That single fact enforces the rules in the brief:

* new writes land only on the new material, old data still verifies on the old
  one (old goes ACTIVE -> RETIRING, which stays readable);
* a value can never land on both, because the swap is one committed state
  change, not two steps with a window between them;
* if the commit fails the ring is exactly as it was — it is impossible to end
  up with zero serving material;
* destruction requires zero active references in the :class:`DependencyLedger`
  and records a verifiable proof snapshot.
"""

from __future__ import annotations

import dataclasses
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

from maskhub.audit.journal import Journal
from maskhub.crypto.ledger import DependencyLedger
from maskhub.crypto.material import (
    Material,
    MaterialState,
    can_transition,
)
from maskhub.errors import Conflict, InvalidRequest, NotFound
from maskhub.store.kvstore import RecordStore

PREFIX = "crypto.material."

# A destroyed tombstone is evidence; keep the proof forever by default.
DESTRUCTION_PROOF_PREFIX = "crypto.destroyed."


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def material_key(identifier: str) -> str:
    return PREFIX + identifier


def proof_key(identifier: str) -> str:
    return DESTRUCTION_PROOF_PREFIX + identifier


@dataclass(frozen=True)
class RotationResult:
    ring: str
    new_active: str
    retired_to: str             # old active, now RETIRING
    rolled_back: bool = False
    reason: str = ""


class KeyRing:
    def __init__(
        self,
        store: RecordStore,
        ledger: DependencyLedger,
        journal: Journal,
        purpose: str,
    ) -> None:
        self._store = store
        self._ledger = ledger
        self._journal = journal
        self.purpose = purpose
        # Serializes read-check-commit sequences (rotation/rollback/retire).
        # The store lock makes each *write* atomic; this lock makes the
        # "exactly one ACTIVE" decision atomic across those writes.
        self._lock = threading.RLock()

    # ---- registration ----------------------------------------------------

    def register(
        self,
        identifier: str,
        key_material: str,
        salt: str,
        valid_from: str = "",
        valid_until: str = "",
        iterations: int | None = None,
    ) -> Material:
        if self.find(identifier) is not None:
            raise Conflict(f"material {identifier} already exists")
        material = Material(
            identifier=identifier,
            purpose=self.purpose,
            key_material=key_material,
            salt=salt,
            valid_from=valid_from,
            valid_until=valid_until,
            iterations=iterations if iterations is not None else 200_000,
            created_at=_now(),
        )
        material = dataclasses.replace(
            material, fingerprint=material.expected_fingerprint()
        )
        self._store.put_json(material_key(identifier), material.as_dict())
        self._journal.append("crypto.material.registered", identifier, self.purpose)
        return material

    def get(self, identifier: str) -> Material:
        material = self.find(identifier)
        if material is None:
            raise NotFound(f"unknown material {identifier}")
        return material

    def find(self, identifier: str) -> Material | None:
        material = Material.from_dict(self._store.get_json(material_key(identifier)))
        if material is not None and material.purpose != self.purpose:
            return None
        return material

    def all(self) -> list[Material]:
        from maskhub.store.query import keys_with_prefix

        materials = [
            material
            for key in keys_with_prefix(self._store, PREFIX)
            if (material := Material.from_dict(self._store.get_json(key))) is not None
            and material.purpose == self.purpose
        ]
        return materials

    def active(self) -> Material | None:
        actives = [m for m in self.all() if m.state_enum is MaterialState.ACTIVE]
        if len(actives) > 1:
            raise Conflict(
                f"ring {self.purpose} has multiple active materials: "
                f"{sorted(m.identifier for m in actives)}"
            )
        return actives[0] if actives else None

    def require_active(self) -> Material:
        material = self.active()
        if material is None:
            raise Conflict(f"ring {self.purpose} has no active material")
        return material

    def readable(self) -> list[Material]:
        """Everything that can still interpret old data (incl. the active one)."""
        return [m for m in self.all() if m.state_enum.can_read]

    def for_verify(self, identifier: str) -> Material:
        """Resolve a material referenced by stored data; rejects destroyed ones."""
        material = self.get(identifier)
        if not material.state_enum.can_read:
            raise InvalidRequest(
                f"material {identifier} is {material.state} and cannot interpret data"
            )
        return material

    # ---- rotation --------------------------------------------------------

    def rotate(self, new_identifier: str) -> RotationResult:
        """Atomically promote ``new_identifier`` and demote the current active.

        Either the whole swap commits or nothing changes; the caller can also
        :meth:`rollback` a committed rotation while the new material has not
        accumulated data yet.
        """
        with self._lock:
            new = self.get(new_identifier)
            if new.purpose != self.purpose:
                raise InvalidRequest(
                    f"material {new_identifier} belongs to purpose {new.purpose}"
                )
            current = self.active()

            if new.state_enum is MaterialState.ACTIVE:
                raise Conflict(f"material {new_identifier} is already active")
            if current is not None and current.identifier == new_identifier:
                raise Conflict(f"material {new_identifier} is the serving material")
            if new.state_enum is not MaterialState.DRAFT:
                # Reactivating a RETIRING candidate belongs to rollback, not rotate.
                raise InvalidRequest(
                    f"material {new_identifier} must be a draft to enter rotation "
                    f"(is {new.state})"
                )

            promoted = new.with_state(MaterialState.ACTIVE)
            writes = {material_key(new_identifier): promoted.as_dict()}
            demoted_id = ""
            if current is not None:
                demoted = current.with_state(MaterialState.RETIRING)
                demoted_id = current.identifier
                writes[material_key(current.identifier)] = demoted.as_dict()

            try:
                self._store.put_json_batch(writes)
            except Exception as exc:  # store already rolled itself back
                self._journal.append(
                    "crypto.rotation.failed",
                    self.purpose,
                    f"candidate={new_identifier}: {exc}",
                )
                return RotationResult(
                    ring=self.purpose,
                    new_active=current.identifier if current else "",
                    retired_to="",
                    rolled_back=True,
                    reason=str(exc),
                )

            self._journal.append(
                "crypto.rotation.completed",
                self.purpose,
                f"active={new_identifier} retiring={demoted_id}",
            )
            return RotationResult(
                ring=self.purpose,
                new_active=new_identifier,
                retired_to=demoted_id,
            )

    def rollback(self, active_identifier: str, previous_identifier: str) -> RotationResult:
        """Undo a rotation: reactivate the prior material, demote the new one.

        Allowed only while the new material has no active data references, so
        a value never has to be interpreted under two different materials.
        """
        with self._lock:
            active = self.get(active_identifier)
            previous = self.get(previous_identifier)
            if active.state_enum is not MaterialState.ACTIVE:
                raise InvalidRequest(f"{active_identifier} is not active")
            if previous.state_enum is not MaterialState.RETIRING:
                raise InvalidRequest(
                    f"{previous_identifier} must be retiring to roll back"
                )
            in_use = self._ledger.active_references_for(active_identifier)
            if in_use:
                raise Conflict(
                    f"cannot roll back: {len(in_use)} artifact(s) already landed on "
                    f"{active_identifier}; migrate them first"
                )
            writes = {
                material_key(active_identifier): active.with_state(
                    MaterialState.RETIRING
                ).as_dict(),
                material_key(previous_identifier): previous.with_state(
                    MaterialState.ACTIVE
                ).as_dict(),
            }
            self._store.put_json_batch(writes)
            self._journal.append(
                "crypto.rotation.rolled_back",
                self.purpose,
                f"active={previous_identifier} retiring={active_identifier}",
            )
            return RotationResult(
                ring=self.purpose,
                new_active=previous_identifier,
                retired_to=active_identifier,
            )

    # ---- retirement / destruction ---------------------------------------

    def retire(self, identifier: str) -> Material:
        """Move a RETIRING material to RETIRED once no data still depends on it.

        RETIRED keeps the secret so historical data remains interpretable; it
        simply leaves the serving/verify set of the ring.
        """
        with self._lock:
            material = self.get(identifier)
            if material.state_enum is not MaterialState.RETIRING:
                raise InvalidRequest(
                    f"material {identifier} cannot retire from {material.state}"
                )
            active_deps = self._ledger.active_references_for(identifier)
            if active_deps:
                raise Conflict(
                    f"material {identifier} still serves {len(active_deps)} artifact(s)"
                )
            retired = material.with_state(MaterialState.RETIRED)
            self._store.put_json(material_key(identifier), retired.as_dict())
            self._journal.append("crypto.material.retired", identifier, self.purpose)
            return retired

    def request_destruction(self, identifier: str) -> Material:
        with self._lock:
            material = self.get(identifier)
            if material.state_enum is MaterialState.DRAFT:
                target = material.with_state(MaterialState.PENDING_DESTRUCTION)
            else:
                if material.state_enum is not MaterialState.RETIRED:
                    raise InvalidRequest(
                        f"material {identifier} cannot be destroyed from {material.state}"
                    )
                active_deps = self._ledger.active_references_for(identifier)
                if active_deps:
                    raise Conflict(
                        f"material {identifier} still has {len(active_deps)} active "
                        f"reference(s); destruction is blocked"
                    )
                target = material.with_state(MaterialState.PENDING_DESTRUCTION)
            self._store.put_json(material_key(identifier), target.as_dict())
            self._journal.append(
                "crypto.material.destruction_requested", identifier, self.purpose
            )
            return target

    def cancel_destruction(self, identifier: str) -> Material:
        with self._lock:
            material = self.get(identifier)
            target = material.with_state(MaterialState.RETIRED)
            self._store.put_json(material_key(identifier), target.as_dict())
            self._journal.append(
                "crypto.material.destruction_cancelled", identifier, self.purpose
            )
            return target

    def destroy(self, identifier: str) -> Material:
        """Zero the secret bytes, retaining a tombstone and a proof snapshot."""
        with self._lock:
            material = self.get(identifier)
            if material.state_enum is not MaterialState.PENDING_DESTRUCTION:
                raise InvalidRequest(
                    f"material {identifier} must be pending destruction (is "
                    f"{material.state})"
                )
            active_deps = self._ledger.active_references_for(identifier)
            if active_deps:
                raise Conflict(
                    f"refusing destruction: {len(active_deps)} active reference(s) "
                    f"still depend on {identifier}"
                )

            proof = self._ledger.proof_snapshot(identifier)
            proof["destroyed_at"] = _now()
            proof["fingerprint"] = material.fingerprint or material.expected_fingerprint()

            tombstone = material.destroyed_tombstone(proof["destroyed_at"])
            writes = {
                material_key(identifier): tombstone.as_dict(),
                proof_key(identifier): proof,
            }
            self._store.put_json_batch(writes)
            self._journal.append(
                "crypto.material.destroyed",
                identifier,
                f"fingerprint={proof['fingerprint']} "
                f"active_references={proof['active_references']}",
            )
            return tombstone

    def destruction_proof(self, identifier: str) -> dict[str, object]:
        proof = self._store.get_json(proof_key(identifier))
        if proof is None:
            raise NotFound(f"no destruction proof for material {identifier}")
        return proof
