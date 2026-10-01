"""Audit journal endpoints."""

from __future__ import annotations

from maskhub.console.http import Request, Response
from maskhub.errors import NotFound


def list_audit(runtime, request: Request) -> Response:
    action = request.query_one("action")
    entries = runtime.journal.by_action(action) if action else runtime.journal.entries()
    return Response.json(
        200,
        {
            "count": len(entries),
            "entries": [entry.as_dict() for entry in entries],
        },
    )


def audit_summary(runtime, request: Request) -> Response:
    return Response.json(200, runtime.journal.summary())


def audit_latest(runtime, request: Request) -> Response:
    entry = runtime.journal.latest()
    if entry is None:
        return Response.json(404, {"error": "journal is empty"})
    return Response.json(200, entry.as_dict())


def audit_by_action(runtime, request: Request) -> Response:
    action = request.query_one("action", "") or ""
    entries = runtime.journal.by_action(action)
    if not entries:
        raise NotFound(f"no journal entries for action {action!r}")
    return Response.json(
        200,
        {"action": action, "count": len(entries), "latest": entries[-1].as_dict()},
    )
