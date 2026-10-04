"""Versioned key material, rotation and destruction lifecycle."""

from __future__ import annotations

from maskhub.crypto.engine import CryptoEngine, parse_envelope
from maskhub.crypto.keyring import KeyRing, RotationResult
from maskhub.crypto.ledger import DependencyLedger, MaterialReference, reference_id
from maskhub.crypto.material import (
    DEFAULT_DERIVE_ITERATIONS,
    Material,
    MaterialState,
    SUPPORTED_PURPOSES,
    can_transition,
    generate_material,
)
from maskhub.crypto.vault import CryptoVault, build_vault

__all__ = [
    "CryptoEngine",
    "CryptoVault",
    "DEFAULT_DERIVE_ITERATIONS",
    "DependencyLedger",
    "KeyRing",
    "Material",
    "MaterialReference",
    "MaterialState",
    "RotationResult",
    "SUPPORTED_PURPOSES",
    "build_vault",
    "can_transition",
    "generate_material",
    "parse_envelope",
    "reference_id",
]
