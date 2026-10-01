"""Masking rule endpoints."""

from __future__ import annotations

from maskhub.console.http import Request, Response
from maskhub.console.validation import optional_text, require_text
from maskhub.errors import InvalidRequest
from maskhub.rule.rule import Rule


def list_rules(runtime, request: Request) -> Response:
    rules = [rule.as_dict() for rule in runtime.rules.all()]
    return Response.json(200, {"count": len(rules), "rules": rules})


def create_rule(runtime, request: Request) -> Response:
    payload = request.json_object()
    parameters = payload.get("parameters", {})
    if not isinstance(parameters, dict):
        raise InvalidRequest("parameters must be an object")
    rule = Rule(
        identifier=require_text(payload, "identifier"),
        field_pattern=require_text(payload, "field_pattern"),
        action=require_text(payload, "action"),
        parameters=parameters,
    )
    runtime.rules.add(rule)
    runtime.journal.append("rule.create", rule.identifier, rule.action)
    return Response.json(200, rule.as_dict())


def remove_rule(runtime, request: Request) -> Response:
    payload = request.json_object()
    identifier = require_text(payload, "identifier")
    runtime.rules.remove(identifier)
    runtime.journal.append("rule.remove", identifier, optional_text(payload, "reason"))
    return Response.json(200, {"removed": identifier})


def match_rule(runtime, request: Request) -> Response:
    payload = request.json_object()
    fields = payload.get("fields")
    if not isinstance(fields, list) or not fields:
        raise InvalidRequest("fields must be a non-empty list")
    names = [require_text({"name": item}, "name") for item in fields]
    return Response.json(200, {"matches": runtime.rules.matching(names)})
