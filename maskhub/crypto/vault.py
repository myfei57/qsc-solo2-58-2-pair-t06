"""Composition helpers for the crypto subsystem."""

from __future__ import annotations

from dataclasses import dataclass

from maskhub.audit.journal import Journal
from maskhub.crypto.engine import CryptoEngine
from maskhub.crypto.keyring import KeyRing
from maskhub.crypto.ledger import DependencyLedger
from maskhub.crypto.material import SUPPORTED_PURPOSES
from maskhub.store.kvstore import RecordStore


@dataclass
class CryptoVault:
    ledger: DependencyLedger
    rings: dict[str, KeyRing]
    engines: dict[str, CryptoEngine]

    def ring(self, purpose: str) -> KeyRing:
        try:
            return self.rings[purpose]
        except KeyError:
            raise KeyError(f"unknown purpose {purpose!r}") from None

    def engine(self, purpose: str) -> CryptoEngine:
        try:
            return self.engines[purpose]
        except KeyError:
            raise KeyError(f"unknown purpose {purpose!r}") from None


def build_vault(store: RecordStore, journal: Journal) -> CryptoVault:
    ledger = DependencyLedger(store)
    rings: dict[str, KeyRing] = {}
    engines: dict[str, CryptoEngine] = {}
    for purpose in SUPPORTED_PURPOSES:
        ring = KeyRing(store, ledger, journal, purpose)
        rings[purpose] = ring
        engines[purpose] = CryptoEngine(ring)
    return CryptoVault(ledger=ledger, rings=rings, engines=engines)
