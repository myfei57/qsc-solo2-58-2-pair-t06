"""Masking policy endpoints."""

from __future__ import annotations

from maskhub.console.http import Request, Response
from maskhub.console.validation import require_text, require_text_list
from maskhub.errors import NotFound
from maskhub.policy.coverage import check_coverage
from maskhub.policy.policy import Policy


def list_policies(runtime, request: Request) -> Response:
    policies = [policy.as_dict() for policy in runtime.policies.all()]
    return Response.json(200, {"count": len(policies), "policies": policies})


def create_policy(runtime, request: Request) -> Response:
    payload = request.json_object()
    policy = Policy(
        identifier=require_text(payload, "identifier"),
        datasets=tuple(require_text_list(payload, "datasets")),
        rules=tuple(require_text_list(payload, "rules")),
    )
    for rule_id in policy.rules:
        runtime.rules.get(rule_id)
    for dataset_id in policy.datasets:
        runtime.datasets.get(dataset_id)
    runtime.policies.save(policy)
    runtime.journal.append("policy.create", policy.identifier, ",".join(policy.datasets))
    return Response.json(200, policy.as_dict())


def activate_policy(runtime, request: Request) -> Response:
    payload = request.json_object()
    identifier = require_text(payload, "identifier")
    policy = runtime.policies.activate(identifier)
    runtime.journal.append("policy.activate", identifier, ",".join(policy.datasets))
    return Response.json(200, policy.as_dict())


def retire_policy(runtime, request: Request) -> Response:
    payload = request.json_object()
    identifier = require_text(payload, "identifier")
    policy = runtime.policies.retire(identifier)
    runtime.journal.append("policy.retire", identifier, policy.state)
    return Response.json(200, policy.as_dict())


def policy_coverage(runtime, request: Request) -> Response:
    payload = request.json_object()
    policy_id = require_text(payload, "policy")
    dataset_id = require_text(payload, "dataset")
    report = check_coverage(
        policy_id, dataset_id, runtime.policies, runtime.datasets, runtime.rules
    )
    return Response.json(200, report.as_dict())


def coverage_gap(runtime, request: Request) -> Response:
    """Report every dataset that no active policy fully covers."""

    gaps: list[dict[str, object]] = []
    for dataset in runtime.datasets.all():
        covered: set[str] = set()
        for policy in runtime.policies.serving():
            if not policy.covers(dataset.identifier):
                continue
            report = check_coverage(
                policy.identifier,
                dataset.identifier,
                runtime.policies,
                runtime.datasets,
                runtime.rules,
            )
            covered.update(report.covered)
        missing = [
            item.name for item in dataset.masked_fields() if item.name not in covered
        ]
        if missing:
            gaps.append({"dataset": dataset.identifier, "uncovered": missing})
    return Response.json(200, {"gaps": gaps, "count": len(gaps)})
