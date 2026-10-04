"""End-to-end checks for the key material lifecycle guarantees."""

from __future__ import annotations

import json
import tempfile
import unittest

from maskhub.catalog.dataset import Dataset
from maskhub.catalog.field import Field
from maskhub.console.errors import error_response
from maskhub.console.handlers import keyring as handlers
from maskhub.console.http import Request
from maskhub.console.routes import build_router
from maskhub.console.runtime import build_runtime
from maskhub.errors import Conflict, Gone, NotFound
from maskhub.keyring import KeyRegistry
from maskhub.store.kvstore import RecordStore


def _store(directory: str) -> RecordStore:
    store = RecordStore(directory)
    store.load()
    return store


class KeyRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.directory = self._tmp.name
        self.keys = KeyRegistry(_store(self.directory))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _rotate_in_second_material(self) -> None:
        self.keys.create_material("salt-1", "hash-salt")
        self.keys.activate("salt-1")
        self.keys.create_material("salt-2", "hash-salt")

    def test_first_activation_sets_write_material(self) -> None:
        self.keys.create_material("salt-1", "hash-salt")
        draft = self.keys.get("salt-1")
        self.assertEqual(draft.state, "draft")
        self.assertEqual(draft.effective_interval, (None, None))
        with self.assertRaises(NotFound):
            self.keys.write_material("hash-salt")

        active = self.keys.activate("salt-1")
        self.assertEqual(active.state, "active")
        self.assertIsNotNone(active.activated_at)
        self.assertEqual(self.keys.write_material("hash-salt").identifier, "salt-1")

    def test_second_activation_requires_rotation(self) -> None:
        self._rotate_in_second_material()
        with self.assertRaises(Conflict):
            self.keys.activate("salt-2")

    def test_material_identifiers_are_unique_across_purposes(self) -> None:
        self.keys.create_material("salt-1", "hash-salt")
        with self.assertRaises(Conflict):
            self.keys.create_material("salt-1", "tokenize-key")

    def test_rotation_commit_is_a_single_cutover(self) -> None:
        self._rotate_in_second_material()
        self.keys.begin_rotation("hash-salt", "salt-2")
        # serving is untouched while the rotation is only prepared
        self.assertEqual(self.keys.write_material("hash-salt").identifier, "salt-1")

        retired, activated = self.keys.commit_rotation("hash-salt")
        self.assertEqual((retired, activated), ("salt-1", "salt-2"))

        ring = self.keys.ring("hash-salt")
        self.assertEqual(ring.write_material, "salt-2")
        old, new = ring.material("salt-1"), ring.material("salt-2")
        self.assertEqual(old.state, "retired")
        self.assertEqual(new.state, "active")
        # intervals meet at one instant; they never overlap
        self.assertEqual(old.retired_at, new.activated_at)
        # data masked with the old material stays explainable
        self.assertEqual(self.keys.read_material("salt-1").identifier, "salt-1")

    def test_abort_restores_pre_rotation_state(self) -> None:
        self._rotate_in_second_material()
        self.keys.begin_rotation("hash-salt", "salt-2")
        target = self.keys.abort_rotation("hash-salt")
        self.assertEqual(target, "salt-2")

        ring = self.keys.ring("hash-salt")
        self.assertIsNone(ring.rotation)
        self.assertEqual(ring.write_material, "salt-1")
        self.assertEqual(ring.material("salt-1").state, "active")
        self.assertEqual(ring.material("salt-2").state, "draft")
        self.assertEqual(self.keys.write_material("hash-salt").identifier, "salt-1")

    def test_commit_without_prepare_conflicts(self) -> None:
        self._rotate_in_second_material()
        with self.assertRaises(Conflict):
            self.keys.commit_rotation("hash-salt")

    def test_second_begin_conflicts(self) -> None:
        self._rotate_in_second_material()
        self.keys.begin_rotation("hash-salt", "salt-2")
        with self.assertRaises(Conflict):
            self.keys.begin_rotation("hash-salt", "salt-2")

    def test_prepared_target_cannot_be_discarded(self) -> None:
        self._rotate_in_second_material()
        self.keys.begin_rotation("hash-salt", "salt-2")
        with self.assertRaises(Conflict):
            self.keys.discard("salt-2")

    def test_no_window_without_usable_material(self) -> None:
        self._rotate_in_second_material()
        self.keys.begin_rotation("hash-salt", "salt-2")

        # a restart in the middle of a rotation must not open a gap
        reloaded = KeyRegistry(_store(self.directory))
        self.assertEqual(reloaded.write_material("hash-salt").identifier, "salt-1")

        retired, activated = reloaded.commit_rotation("hash-salt")
        self.assertEqual((retired, activated), ("salt-1", "salt-2"))
        self.assertEqual(reloaded.write_material("hash-salt").identifier, "salt-2")

    def test_destroy_waits_until_nothing_depends_on_it(self) -> None:
        self._rotate_in_second_material()
        self.keys.attach_lease("hash-salt", "orders")
        self.keys.begin_rotation("hash-salt", "salt-2")
        self.keys.commit_rotation("hash-salt")

        with self.assertRaises(Conflict) as ctx:
            self.keys.destroy("salt-1")
        self.assertIn("orders", str(ctx.exception))

        self.assertTrue(self.keys.detach_lease("salt-1", "orders"))
        destroyed = self.keys.destroy("salt-1")
        self.assertEqual(destroyed.state, "destroyed")
        self.assertIsNone(destroyed.secret)
        self.assertIsNotNone(destroyed.destroyed_at)
        with self.assertRaises(Gone):
            self.keys.read_material("salt-1")
        # the tombstone keeps the effective interval provable
        tombstone = self.keys.get("salt-1")
        self.assertIsNotNone(tombstone.activated_at)
        self.assertIsNotNone(tombstone.retired_at)

    def test_destroy_rejects_active_and_draft(self) -> None:
        self._rotate_in_second_material()
        with self.assertRaises(Conflict):
            self.keys.destroy("salt-2")  # draft: discard instead
        with self.assertRaises(Conflict):
            self.keys.destroy("salt-1")  # active: rotate first

    def test_lease_lands_on_current_write_material(self) -> None:
        self._rotate_in_second_material()
        material, added = self.keys.attach_lease("hash-salt", "orders")
        self.assertTrue(added)
        self.assertEqual(material.identifier, "salt-1")

        self.keys.begin_rotation("hash-salt", "salt-2")
        self.keys.commit_rotation("hash-salt")
        material, _ = self.keys.attach_lease("hash-salt", "orders")
        self.assertEqual(material.identifier, "salt-2")

    def test_attach_to_destroyed_material_is_gone(self) -> None:
        self._rotate_in_second_material()
        self.keys.begin_rotation("hash-salt", "salt-2")
        self.keys.commit_rotation("hash-salt")
        self.keys.destroy("salt-1")
        with self.assertRaises(Gone):
            self.keys.attach_lease("hash-salt", "orders", material_id="salt-1")

    def test_destroy_erases_secret_from_storage(self) -> None:
        self._rotate_in_second_material()
        self.keys.begin_rotation("hash-salt", "salt-2")
        self.keys.commit_rotation("hash-salt")
        self.keys.destroy("salt-1")

        reloaded = KeyRegistry(_store(self.directory))
        self.assertIsNone(reloaded.get("salt-1").secret)
        raw = reloaded.ring("hash-salt").as_dict()
        for material in raw["materials"]:
            if material["identifier"] == "salt-1":
                self.assertIsNone(material["secret"])


class KeyringHandlersTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.runtime = build_runtime(_store(self._tmp.name))
        self.runtime.datasets.save(
            Dataset(
                identifier="orders",
                source="src",
                owner="ops",
                fields=(Field(name="email", sensitivity="personal", declared_by="ops"),),
            )
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _call(self, handler, payload: dict | None = None, query: dict | None = None):
        body = json.dumps(payload).encode() if payload is not None else b""
        request = Request(method="POST", path="/", query=query or {}, headers={}, body=body)
        response = handler(self.runtime, request)
        return response.status, json.loads(response.payload.decode())

    def _run_rotation_and_destroy(self) -> None:
        self._call(handlers.create_material, {"identifier": "salt-1", "purpose": "hash-salt"})
        self._call(handlers.activate_material, {"identifier": "salt-1"})
        self._call(handlers.attach_lease, {"purpose": "hash-salt", "dataset": "orders"})
        self._call(handlers.create_material, {"identifier": "salt-2", "purpose": "hash-salt"})
        self._call(handlers.begin_rotation, {"purpose": "hash-salt", "target": "salt-2"})
        self._call(handlers.commit_rotation, {"purpose": "hash-salt"})
        with self.assertRaises(Conflict):
            self._call(handlers.destroy_material, {"identifier": "salt-1"})
        self._call(handlers.detach_lease, {"material": "salt-1", "dataset": "orders"})
        self._call(handlers.destroy_material, {"identifier": "salt-1"})

    def test_create_hands_out_secret_once(self) -> None:
        _, body = self._call(
            handlers.create_material, {"identifier": "salt-1", "purpose": "hash-salt"}
        )
        self.assertTrue(body["secret"])
        _, listed = self._call(handlers.list_materials, query={"purpose": ["hash-salt"]})
        self.assertNotIn("secret", listed["materials"][0])
        self.assertTrue(listed["materials"][0]["has_secret"])

    def test_rotation_flow_is_journaled(self) -> None:
        self._run_rotation_and_destroy()
        actions = [entry.action for entry in self.runtime.journal.entries()]
        self.assertEqual(
            actions,
            [
                "key.create",
                "key.activate",
                "key.lease.attach",
                "key.create",
                "key.rotation.begin",
                "key.rotation.commit",
                "key.lease.detach",
                "key.destroy",
            ],
        )
        commit = self.runtime.journal.by_action("key.rotation.commit")[0]
        self.assertEqual(commit.detail, "salt-1 -> salt-2")
        destroy = self.runtime.journal.by_action("key.destroy")[0]
        self.assertIn("leases=0", destroy.detail)

    def test_destroy_precondition_is_replayable_from_journal(self) -> None:
        self._run_rotation_and_destroy()
        leases: set[str] = set()
        for entry in self.runtime.journal.entries():
            if entry.action == "key.lease.attach":
                leases.add(entry.detail)
            elif entry.action == "key.lease.detach":
                leases.discard(entry.detail)
            elif entry.action == "key.destroy":
                # provable after the fact: nothing in service depended on it
                self.assertEqual(leases, set(), f"unreleased leases at {entry.subject} destroy")

    def test_gone_maps_to_http_410(self) -> None:
        response = error_response(Gone("key material salt-1 was destroyed"))
        self.assertEqual(response.status, 410)

    def test_router_serves_keyring_endpoints(self) -> None:
        router = build_router(self.runtime)
        response = router.dispatch(Request(method="GET", path="/keyrings", query={}, headers={}))
        self.assertEqual(response.status, 200)
        response = router.dispatch(
            Request(
                method="POST",
                path="/keyring/materials",
                query={},
                headers={},
                body=json.dumps({"identifier": "salt-1", "purpose": "hash-salt"}).encode(),
            )
        )
        self.assertEqual(response.status, 200)
        response = router.dispatch(Request(method="GET", path="/keyrings", query={}, headers={}))
        rings = json.loads(response.payload.decode())["rings"]
        self.assertEqual(rings[0]["purpose"], "hash-salt")


if __name__ == "__main__":
    unittest.main()
