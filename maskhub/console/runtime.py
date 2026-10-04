"""Composition root for the gateway."""

from __future__ import annotations

from dataclasses import dataclass

from maskhub.audit.journal import Journal
from maskhub.catalog.dataset import DatasetRegistry
from maskhub.crypto import CryptoVault, build_vault
from maskhub.policy.registry import PolicyRegistry
from maskhub.rule.registry import RuleRegistry
from maskhub.source.registry import SourceRegistry
from maskhub.store.kvstore import RecordStore


@dataclass
class Runtime:
    store: RecordStore
    journal: Journal
    sources: SourceRegistry
    datasets: DatasetRegistry
    rules: RuleRegistry
    policies: PolicyRegistry
    vault: CryptoVault


def build_runtime(store: RecordStore) -> Runtime:
    journal = Journal(store)
    return Runtime(
        store=store,
        journal=journal,
        sources=SourceRegistry(store),
        datasets=DatasetRegistry(store),
        rules=RuleRegistry(store),
        policies=PolicyRegistry(store),
        vault=build_vault(store, journal),
    )
