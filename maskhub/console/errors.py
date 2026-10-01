"""Domain error to HTTP response mapping."""

from __future__ import annotations

from maskhub.console.http import Response
from maskhub.errors import Conflict, InvalidRequest, NotFound


def error_response(exc: Exception) -> Response:
    if isinstance(exc, NotFound):
        return Response.json(404, {"error": str(exc)})
    if isinstance(exc, Conflict):
        return Response.json(409, {"error": str(exc)})
    if isinstance(exc, (InvalidRequest, ValueError)):
        return Response.json(400, {"error": str(exc)})
    return Response.json(500, {"error": str(exc)})
