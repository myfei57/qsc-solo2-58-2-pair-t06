"""Dataset and field catalog endpoints."""

from __future__ import annotations

from maskhub.catalog.dataset import Dataset
from maskhub.catalog.field import Field
from maskhub.console.http import Request, Response
from maskhub.console.validation import require_text
from maskhub.errors import InvalidRequest


def _parse_fields(runtime, owner: str, raw_fields: object) -> tuple[Field, ...]:
    if not isinstance(raw_fields, list) or not raw_fields:
        raise InvalidRequest("fields must be a non-empty list")
    fields: list[Field] = []
    for raw in raw_fields:
        if not isinstance(raw, dict):
            raise InvalidRequest("each field must be an object")
        fields.append(
            Field(
                name=require_text(raw, "name"),
                sensitivity=require_text(raw, "sensitivity"),
                declared_by=owner,
            )
        )
    return tuple(fields)


def list_datasets(runtime, request: Request) -> Response:
    datasets = [dataset.as_dict() for dataset in runtime.datasets.all()]
    return Response.json(200, {"count": len(datasets), "datasets": datasets})


def get_dataset(runtime, request: Request) -> Response:
    identifier = request.query_one("dataset", "") or ""
    return Response.json(200, runtime.datasets.get(identifier).as_dict())


def register_dataset(runtime, request: Request) -> Response:
    payload = request.json_object()
    identifier = require_text(payload, "identifier")
    source = require_text(payload, "source")
    owner = require_text(payload, "owner")
    runtime.sources.get(source)
    dataset = Dataset(
        identifier=identifier,
        source=source,
        owner=owner,
        fields=_parse_fields(runtime, owner, payload.get("fields")),
    )
    runtime.datasets.save(dataset)
    runtime.journal.append("dataset.register", identifier, source)
    return Response.json(200, dataset.as_dict())
