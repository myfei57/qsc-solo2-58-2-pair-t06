"""Data source endpoints."""

from __future__ import annotations

from maskhub.console.http import Request, Response
from maskhub.console.validation import require_text
from maskhub.source.registry import Source, SourceStatus


def list_sources(runtime, request: Request) -> Response:
    sources = [source.as_dict() for source in runtime.sources.all()]
    return Response.json(200, {"count": len(sources), "sources": sources})


def register_source(runtime, request: Request) -> Response:
    payload = request.json_object()
    source = Source(
        identifier=require_text(payload, "identifier"),
        kind=require_text(payload, "kind"),
        endpoint=require_text(payload, "endpoint"),
    )
    runtime.sources.register(source)
    runtime.journal.append("source.register", source.identifier, source.kind)
    return Response.json(200, source.as_dict())


def set_source_status(runtime, request: Request) -> Response:
    payload = request.json_object()
    identifier = require_text(payload, "identifier")
    raw_status = require_text(payload, "status")
    try:
        status = SourceStatus(raw_status)
    except ValueError as exc:
        from maskhub.errors import InvalidRequest

        raise InvalidRequest(f"unknown source status {raw_status!r}") from exc
    updated = runtime.sources.set_status(identifier, status)
    runtime.journal.append("source.status", identifier, status.value)
    return Response.json(200, updated.as_dict())
