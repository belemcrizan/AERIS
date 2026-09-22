"""Domain errors. These mark policy boundaries, not generic crashes."""

from __future__ import annotations


class AerisError(Exception):
    """Base class for expected AERIS policy failures."""


class UnsafeRetry(AerisError):
    """Automatic or human retry would repeat a non-idempotent side effect."""


class UnsafeReroute(AerisError):
    """Reroute would abandon a committed side effect that cannot be compensated."""


class UnauthorizedIntervention(AerisError):
    """Caller role is not allowed to apply this control action."""


class BudgetExceeded(AerisError):
    """The proposed intervention would exceed mission budget."""


class NoRouteAvailable(AerisError):
    """No candidate airway remains."""


class CompensationFailed(AerisError):
    """A required compensation did not succeed."""


class RecorderIntegrityError(AerisError):
    """The append-only hash chain is inconsistent."""


class UnsupportedRuntimeCapability(AerisError):
    """The runtime cannot perform the requested intervention."""
