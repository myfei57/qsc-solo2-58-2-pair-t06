"""Static check that an active policy covers every declared sensitive field."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from maskhub.catalog.dataset import DatasetRegistry
from maskhub.errors import NotFound
from maskhub.policy.registry import PolicyRegistry
from maskhub.rule.registry import RuleRegistry


@dataclass(frozen=True)
class CoverageReport:
    policy: str
    dataset: str
    covered: tuple[str, ...]
    uncovered: tuple[str, ...]
    unmatched_rules: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.uncovered

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["complete"] = self.complete
        return payload


def check_coverage(
    policy_id: str,
    dataset_id: str,
    policies: PolicyRegistry,
    datasets: DatasetRegistry,
    rules: RuleRegistry,
) -> CoverageReport:
    policy = policies.get(policy_id)
    if not policy.covers(dataset_id):
        raise NotFound(f"policy {policy_id} does not target dataset {dataset_id}")
    dataset = datasets.get(dataset_id)

    sensitive = [item.name for item in dataset.masked_fields()]
    covered: list[str] = []
    uncovered: list[str] = []
    used: set[str] = set()
    for name in sensitive:
        hit = False
        for rule_id in policy.rules:
            rule = rules.get(rule_id)
            if rule.matches(name):
                hit = True
                used.add(rule_id)
        (covered if hit else uncovered).append(name)
    unmatched = tuple(sorted(set(policy.rules) - used))
    return CoverageReport(
        policy=policy.identifier,
        dataset=dataset.identifier,
        covered=tuple(covered),
        uncovered=tuple(uncovered),
        unmatched_rules=unmatched,
    )
