"""Key and salt lifecycle endpoints."""

from __future__ import annotations

from maskhub.console.http import Request, Response
from maskhub.console.validation import optional_text, require_text
from maskhub.errors import InvalidRequest
from maskhub.keyring.material import KeyRing


def _ring_view(ring: KeyRing) -> dict[str, object]:
    return {
        "purpose": ring.purpose,
        "write_material": ring.write_material,
        "rotation": ring.rotation.as_dict() if ring.rotation else None,
        "materials": [material.public_dict() for material in ring.materials],
    }


def list_rings(runtime, request: Request) -> Response:
    rings = [_ring_view(ring) for ring in runtime.keyring.rings()]
    return Response.json(200, {"count": len(rings), "rings": rings})


def get_ring(runtime, request: Request) -> Response:
    purpose = request.query_one("purpose", "") or ""
    if not purpose:
        raise InvalidRequest("purpose query parameter is required")
    return Response.json(200, _ring_view(runtime.keyring.ring(purpose)))


def list_materials(runtime, request: Request) -> Response:
    materials = runtime.keyring.materials(request.query_one("purpose"))
    views = [material.public_dict() for material in materials]
    return Response.json(200, {"count": len(views), "materials": views})


def create_material(runtime, request: Request) -> Response:
    payload = request.json_object()
    material = runtime.keyring.create_material(
        identifier=require_text(payload, "identifier"),
        purpose=require_text(payload, "purpose"),
        secret=optional_text(payload, "secret") or None,
    )
    runtime.journal.append("key.create", material.identifier, material.purpose)
    body = material.public_dict()
    body["secret"] = material.secret  # handed out once, at creation time
    return Response.json(200, body)


def activate_material(runtime, request: Request) -> Response:
    payload = request.json_object()
    material = runtime.keyring.activate(require_text(payload, "identifier"))
    runtime.journal.append("key.activate", material.identifier, material.purpose)
    return Response.json(200, material.public_dict())


def discard_material(runtime, request: Request) -> Response:
    payload = request.json_object()
    identifier = require_text(payload, "identifier")
    runtime.keyring.discard(identifier)
    runtime.journal.append("key.discard", identifier, optional_text(payload, "reason"))
    return Response.json(200, {"discarded": identifier})


def destroy_material(runtime, request: Request) -> Response:
    payload = request.json_object()
    material = runtime.keyring.destroy(require_text(payload, "identifier"))
    runtime.journal.append(
        "key.destroy",
        material.identifier,
        f"purpose={material.purpose} leases=0 "
        f"retired_at={material.retired_at} destroyed_at={material.destroyed_at}",
    )
    return Response.json(200, material.public_dict())


def begin_rotation(runtime, request: Request) -> Response:
    payload = request.json_object()
    purpose = require_text(payload, "purpose")
    target = require_text(payload, "target")
    current = runtime.keyring.ring(purpose).write_material
    rotation = runtime.keyring.begin_rotation(purpose, target)
    runtime.journal.append("key.rotation.begin", purpose, f"{current} -> {rotation.target}")
    return Response.json(200, rotation.as_dict())


def commit_rotation(runtime, request: Request) -> Response:
    payload = request.json_object()
    purpose = require_text(payload, "purpose")
    retired, activated = runtime.keyring.commit_rotation(purpose)
    runtime.journal.append("key.rotation.commit", purpose, f"{retired} -> {activated}")
    return Response.json(200, _ring_view(runtime.keyring.ring(purpose)))


def abort_rotation(runtime, request: Request) -> Response:
    payload = request.json_object()
    purpose = require_text(payload, "purpose")
    target = runtime.keyring.abort_rotation(purpose)
    reason = optional_text(payload, "reason")
    runtime.journal.append("key.rotation.abort", purpose, f"target={target} reason={reason}")
    return Response.json(200, {"purpose": purpose, "aborted": target})


def attach_lease(runtime, request: Request) -> Response:
    payload = request.json_object()
    purpose = require_text(payload, "purpose")
    dataset = require_text(payload, "dataset")
    material_id = optional_text(payload, "material") or None
    runtime.datasets.get(dataset)
    material, added = runtime.keyring.attach_lease(purpose, dataset, material_id)
    if added:
        runtime.journal.append("key.lease.attach", material.identifier, dataset)
    body = material.public_dict()
    body["lease_added"] = added
    return Response.json(200, body)


def detach_lease(runtime, request: Request) -> Response:
    payload = request.json_object()
    identifier = require_text(payload, "material")
    dataset = require_text(payload, "dataset")
    removed = runtime.keyring.detach_lease(identifier, dataset)
    if removed:
        runtime.journal.append("key.lease.detach", identifier, dataset)
    return Response.json(
        200, {"material": identifier, "dataset": dataset, "detached": removed}
    )
