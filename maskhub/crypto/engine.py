"""Versioned hashing / tokenization on top of a key ring.

Every output is an **envelope** that records which material produced it::

    mh1$<material_identifier>$<purpose>$<digest hex>

Because the identifier travels with the value, verifying old data or an
exported copy resolves *that exact* material instead of whatever happens to be
active now. Writes only ever use the ring's single ACTIVE material, so during a
rotation a value cannot land once under the old material and once under the
new one.
"""

from __future__ import annotations

import hashlib
import hmac

from maskhub.crypto.keyring import KeyRing
from maskhub.crypto.material import Material
from maskhub.errors import InvalidRequest

ENVELOPE_VERSION = "mh1"
HASH_DIGEST_BYTES = 32
TOKEN_BYTES = 16


def _derive_key(material: Material) -> bytes:
    """Salt + key -> purpose-bound derived key.

    PBKDF2-HMAC-SHA256 with the material's per-record salt, so swapping either
    the key or the salt is a distinct material version and never silently
    changes a batch in place.
    """
    return hashlib.pbkdf2_hmac(
        "sha256",
        material.key_bytes,
        material.salt_bytes,
        material.iterations,
    )


def _digest(material: Material, value: str, context: str) -> bytes:
    derived = _derive_key(material)
    message = material.purpose.encode() + b"\x1f" + context.encode() + b"\x1f"
    message += value.encode("utf-8")
    return hmac.new(derived, message, hashlib.sha256).digest()


def envelope(material_identifier: str, purpose: str, digest: bytes) -> str:
    return f"{ENVELOPE_VERSION}${material_identifier}${purpose}${digest.hex()}"


def parse_envelope(value: str) -> tuple[str, str, bytes]:
    parts = value.split("$")
    if len(parts) != 4 or parts[0] != ENVELOPE_VERSION:
        raise InvalidRequest("not a maskhub crypto envelope")
    version, identifier, purpose, digest_hex = parts
    try:
        return identifier, purpose, bytes.fromhex(digest_hex)
    except ValueError as exc:
        raise InvalidRequest("malformed envelope digest") from exc


class CryptoEngine:
    """Stateless operations that always pin the material used."""

    def __init__(self, ring: KeyRing) -> None:
        self._ring = ring

    def hash_value(self, value: str, context: str = "") -> str:
        material = self._ring.require_active()
        digest = _digest(material, value, context)
        return envelope(material.identifier, self._ring.purpose, digest)

    def tokenize(self, value: str, context: str = "") -> str:
        material = self._ring.require_active()
        digest = _digest(material, value, context)[:TOKEN_BYTES]
        return envelope(material.identifier, self._ring.purpose, digest)

    def verify(self, value: str, candidate: str, context: str = "") -> bool:
        """Check ``candidate`` against an existing envelope using ITS material.

        The lookup goes through ``for_verify``: ACTIVE, RETIRING and RETIRED
        materials all still verify; a DESTROYED material is refused rather
        than silently producing a mismatch.
        """
        identifier, _purpose, expected = parse_envelope(candidate)
        material = self._ring.for_verify(identifier)
        actual = _digest(material, value, context)[: len(expected)]
        return hmac.compare_digest(actual, expected)

    def explains(self, candidate: str) -> bool:
        """Whether an envelope can still be interpreted (material not destroyed)."""
        try:
            identifier, _purpose, _digest_value = parse_envelope(candidate)
        except InvalidRequest:
            return False
        material = self._ring.find(identifier)
        return material is not None and material.state_enum.can_read
