"""Domain errors shared across the gateway."""

from __future__ import annotations


class MaskHubError(RuntimeError):
    """Base class for all gateway domain errors."""


class NotFound(MaskHubError):
    """Raised when a referenced object does not exist."""


class Conflict(MaskHubError):
    """Raised when an operation conflicts with current state."""


class InvalidRequest(MaskHubError):
    """Raised when a request violates the gateway contract."""
