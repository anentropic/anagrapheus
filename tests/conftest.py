"""
Shared fixtures and the no-live-model guard.

ALLOW_MODEL_REQUESTS is switched off for the whole suite, so a stray call to a real
provider fails the test instead of silently billing anyone. The ordinary tests cannot
spend money; they are not merely expected not to. Only the tier-3 live smoke tests lift
the guard, and only for themselves.
"""

from pydantic_ai import models

models.ALLOW_MODEL_REQUESTS = False
