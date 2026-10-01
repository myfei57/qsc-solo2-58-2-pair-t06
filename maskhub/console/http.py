"""Request and response value objects."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from maskhub.errors import InvalidRequest


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    query: Mapping[str, list[str]]
    headers: Mapping[str, str]
    body: bytes = b""

    def query_one(self, name: str, default: str | None = None) -> str | None:
        values = self.query.get(name)
        return values[0] if values else default

    def json_object(self) -> dict[str, Any]:
        if not self.body:
            return {}
        try:
            payload = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise InvalidRequest(f"invalid JSON body: {exc}") from exc
        if not isinstance(payload, dict):
            raise InvalidRequest("request body must be a JSON object")
        return payload


@dataclass(frozen=True)
class Response:
    status: int
    payload: bytes
    content_type: str = "application/json; charset=utf-8"
    headers: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, status: int, value: Any) -> "Response":
        return cls(status=status, payload=json.dumps(value, ensure_ascii=False).encode("utf-8"))
