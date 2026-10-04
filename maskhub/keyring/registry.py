"""Versioned key material with atomic rotation and gated destruction."""

from __future__ import annotations

import secrets
from datetime import datetime, timezone

from maskhub.errors import Conflict, Gone, NotFound
from maskhub.keyring.material import KeyMaterial, KeyRing, MaterialState, Rotation
from maskhub.store.kvstore import RecordStore
from maskhub.store.query import keys_with_prefix

PREFIX = "keyring."


def ring_key(purpose: str) -> str:
    return PREFIX + purpose


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class KeyRegistry:
    """Lifecycle of key/salt material, one ring document per purpose.

    Invariants enforced here:
    * at most one active (write) material per purpose, switched atomically;
    * retired material stays readable until it is destroyed;
    * destruction requires retirement and zero in-service leases.
    """

    def __init__(self, store: RecordStore) -> None:
        self._store = store

    # -- queries -----------------------------------------------------------

    def ring(self, purpose: str) -> KeyRing:
        ring = self._load(purpose)
        if ring is None:
            raise NotFound(f"no key material for purpose {purpose!r}")
        return ring

    def rings(self) -> list[KeyRing]:
        found = []
        for key in keys_with_prefix(self._store, PREFIX):
            ring = KeyRing.from_dict(self._store.get_json(key))
            if ring is not None:
                found.append(ring)
        return found

    def find(self, identifier: str) -> KeyMaterial | None:
        for ring in self.rings():
            material = ring.material(identifier)
            if material is not None:
                return material
        return None

    def get(self, identifier: str) -> KeyMaterial:
        material = self.find(identifier)
        if material is None:
            raise NotFound(f"unknown key material {identifier}")
        return material

    def materials(self, purpose: str | None = None) -> list[KeyMaterial]:
        rings = [self.ring(purpose)] if purpose is not None else self.rings()
        return [material for ring in rings for material in ring.materials]

    def write_material(self, purpose: str) -> KeyMaterial:
        """The material that new data must be masked with right now."""

        material = self.ring(purpose).active()
        if material is None:
            raise NotFound(f"purpose {purpose!r} has no active key material")
        return material

    def read_material(self, identifier: str) -> KeyMaterial:
        """The material that explains existing data, if it still can."""

        material = self.get(identifier)
        state = material.state_enum
        if state is MaterialState.DESTROYED:
            raise Gone(f"key material {identifier} was destroyed at {material.destroyed_at}")
        if state is MaterialState.DRAFT:
            raise Conflict(f"key material {identifier} never served data")
        return material

    # -- lifecycle ---------------------------------------------------------

    def create_material(
        self, identifier: str, purpose: str, secret: str | None = None
    ) -> KeyMaterial:
        material = KeyMaterial(
            identifier=identifier,
            purpose=purpose,
            secret=secret if secret is not None else secrets.token_hex(32),
            created_at=_now(),
        )
        if self.find(material.identifier) is not None:
            raise Conflict(f"key material {material.identifier} already exists")

        def mutate(document: object) -> tuple[object, KeyMaterial]:
            ring = KeyRing.from_dict(document) or KeyRing(purpose=material.purpose)
            if ring.material(material.identifier) is not None:
                raise Conflict(f"key material {material.identifier} already exists")
            return ring.with_material(material).as_dict(), material

        return self._store.update_json(ring_key(material.purpose), mutate)

    def discard(self, identifier: str) -> None:
        """Remove a draft that never served data."""

        purpose = self._purpose_of(identifier)

        def mutate(document: object) -> tuple[object, None]:
            ring = KeyRing.from_dict(document)
            material = ring.material(identifier) if ring else None
            if material is None:
                raise NotFound(f"unknown key material {identifier}")
            if material.state_enum is not MaterialState.DRAFT:
                raise Conflict(
                    f"only draft material can be discarded; {identifier} is {material.state}"
                )
            if ring.rotation is not None and ring.rotation.target == identifier:
                raise Conflict(
                    f"material {identifier} is the prepared rotation target; abort the rotation first"
                )
            return ring.without_material(identifier).as_dict(), None

        self._store.update_json(ring_key(purpose), mutate)

    def activate(self, identifier: str) -> KeyMaterial:
        """Activate the first material of a purpose; later material rotates in."""

        purpose = self._purpose_of(identifier)

        def mutate(document: object) -> tuple[object, KeyMaterial]:
            ring = KeyRing.from_dict(document)
            material = ring.material(identifier) if ring else None
            if material is None:
                raise NotFound(f"unknown key material {identifier}")
            if material.state_enum is not MaterialState.DRAFT:
                raise Conflict(
                    f"material {identifier} is {material.state}; only draft material can be activated"
                )
            if ring.write_material is not None:
                raise Conflict(
                    f"purpose {ring.purpose!r} already writes with {ring.write_material}; use rotation"
                )
            activated = material.activated(_now())
            ring = ring.with_material(activated).with_write_material(identifier)
            return ring.as_dict(), activated

        return self._store.update_json(ring_key(purpose), mutate)

    def begin_rotation(self, purpose: str, target: str) -> Rotation:
        """Prepare a cutover. Serving is untouched until commit."""

        def mutate(document: object) -> tuple[object, Rotation]:
            ring = KeyRing.from_dict(document)
            if ring is None:
                raise NotFound(f"no key material for purpose {purpose!r}")
            if ring.write_material is None:
                raise Conflict(f"purpose {purpose!r} has no active material; activate one instead")
            if ring.rotation is not None:
                raise Conflict(
                    f"rotation to {ring.rotation.target} already prepared for purpose {purpose!r}"
                )
            material = ring.material(target)
            if material is None:
                raise NotFound(f"unknown key material {target} for purpose {purpose!r}")
            if material.state_enum is not MaterialState.DRAFT:
                raise Conflict(f"rotation target {target} is {material.state}; must be draft")
            rotation = Rotation(target=target, began_at=_now())
            return ring.with_rotation(rotation).as_dict(), rotation

        return self._store.update_json(ring_key(purpose), mutate)

    def commit_rotation(self, purpose: str) -> tuple[str, str]:
        """Atomically switch writes to the prepared target and retire the old.

        This is the single commit point of a rotation: before it the old
        material serves, after it the new one does. There is no window with
        zero or two write materials.
        """

        def mutate(document: object) -> tuple[object, tuple[str, str]]:
            ring = KeyRing.from_dict(document)
            if ring is None or ring.rotation is None:
                raise Conflict(f"no prepared rotation for purpose {purpose!r}")
            current = ring.active()
            if current is None or current.state_enum is not MaterialState.ACTIVE:
                raise Conflict(f"purpose {purpose!r} has no active material to rotate from")
            target = ring.material(ring.rotation.target)
            if target is None or target.state_enum is not MaterialState.DRAFT:
                raise Conflict(f"rotation target for purpose {purpose!r} is no longer a draft")
            now = _now()
            ring = ring.with_material(current.retired(now))
            ring = ring.with_material(target.activated(now))
            ring = ring.with_write_material(target.identifier).with_rotation(None)
            return ring.as_dict(), (current.identifier, target.identifier)

        return self._store.update_json(ring_key(purpose), mutate)

    def abort_rotation(self, purpose: str) -> str:
        """Drop a prepared rotation, leaving serving exactly as it was."""

        def mutate(document: object) -> tuple[object, str]:
            ring = KeyRing.from_dict(document)
            if ring is None or ring.rotation is None:
                raise Conflict(f"no prepared rotation for purpose {purpose!r}")
            target = ring.rotation.target
            return ring.with_rotation(None).as_dict(), target

        return self._store.update_json(ring_key(purpose), mutate)

    def attach_lease(
        self, purpose: str, dataset: str, material_id: str | None = None
    ) -> tuple[KeyMaterial, bool]:
        """Pin in-service data to the material it was masked with.

        Without an explicit ``material_id`` the lease lands on the current
        write material, which is what the data plane just used.
        """

        def mutate(document: object) -> tuple[object, tuple[KeyMaterial, bool]]:
            ring = KeyRing.from_dict(document)
            if ring is None:
                raise NotFound(f"no key material for purpose {purpose!r}")
            identifier = material_id or ring.write_material
            if identifier is None:
                raise Conflict(f"purpose {purpose!r} has no active material to pin data to")
            material = ring.material(identifier)
            if material is None:
                raise NotFound(f"unknown key material {identifier} for purpose {purpose!r}")
            state = material.state_enum
            if state is MaterialState.DESTROYED:
                raise Gone(f"key material {identifier} was destroyed at {material.destroyed_at}")
            if state is MaterialState.DRAFT:
                raise Conflict(f"draft material {identifier} never served data")
            if dataset in material.leases:
                return None, (material, False)
            updated = material.with_lease(dataset)
            return ring.with_material(updated).as_dict(), (updated, True)

        return self._store.update_json(ring_key(purpose), mutate)

    def detach_lease(self, identifier: str, dataset: str) -> bool:
        purpose = self._purpose_of(identifier)

        def mutate(document: object) -> tuple[object, bool]:
            ring = KeyRing.from_dict(document)
            material = ring.material(identifier) if ring else None
            if material is None:
                raise NotFound(f"unknown key material {identifier}")
            if dataset not in material.leases:
                return None, False
            return ring.with_material(material.without_lease(dataset)).as_dict(), True

        return self._store.update_json(ring_key(purpose), mutate)

    def destroy(self, identifier: str) -> KeyMaterial:
        """Erase the secret of a retired material that nothing depends on.

        The tombstone (interval, timestamps) stays so the lifecycle remains
        auditable; the secret is gone for good.
        """

        purpose = self._purpose_of(identifier)

        def mutate(document: object) -> tuple[object, KeyMaterial]:
            ring = KeyRing.from_dict(document)
            material = ring.material(identifier) if ring else None
            if material is None:
                raise NotFound(f"unknown key material {identifier}")
            state = material.state_enum
            if state is MaterialState.ACTIVE:
                raise Conflict(f"material {identifier} is the active write material; rotate first")
            if state is MaterialState.DRAFT:
                raise Conflict(f"draft material {identifier} never served data; discard it instead")
            if state is MaterialState.DESTROYED:
                raise Conflict(
                    f"material {identifier} was already destroyed at {material.destroyed_at}"
                )
            if material.leases:
                raise Conflict(
                    f"material {identifier} still serves {len(material.leases)} dataset(s): "
                    + ", ".join(material.leases)
                )
            destroyed = material.destroyed(_now())
            return ring.with_material(destroyed).as_dict(), destroyed

        return self._store.update_json(ring_key(purpose), mutate)

    # -- internals ---------------------------------------------------------

    def _load(self, purpose: str) -> KeyRing | None:
        return KeyRing.from_dict(self._store.get_json(ring_key(purpose)))

    def _purpose_of(self, identifier: str) -> str:
        for ring in self.rings():
            if ring.material(identifier) is not None:
                return ring.purpose
        raise NotFound(f"unknown key material {identifier}")
