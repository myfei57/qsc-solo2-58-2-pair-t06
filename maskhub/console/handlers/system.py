"""Health and status endpoints."""

from __future__ import annotations

from maskhub.console.http import Request, Response
from maskhub.store.query import count_with_prefix, key_metrics


def health(runtime, request: Request) -> Response:
    return Response.json(200, {"status": "ok", "service": "maskhub"})


def status(runtime, request: Request) -> Response:
    metrics = key_metrics(runtime.store)
    return Response.json(
        200,
        {
            "keys": metrics["keys"],
            "bytes": metrics["bytes"],
            "sources": count_with_prefix(runtime.store, "source."),
            "datasets": count_with_prefix(runtime.store, "dataset."),
            "rules": count_with_prefix(runtime.store, "rule."),
            "policies": count_with_prefix(runtime.store, "policy."),
            "serving_policies": [policy.identifier for policy in runtime.policies.serving()],
            "audit_actions": runtime.journal.summary(),
            "sensitive_surface": runtime.datasets.sensitive_surface(),
        },
    )
