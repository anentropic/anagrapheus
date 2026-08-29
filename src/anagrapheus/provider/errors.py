"""Provider error types."""

from __future__ import annotations

__all__ = ["ProviderError"]


class ProviderError(Exception):
    """Base class for all provider errors."""
