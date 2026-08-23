"""
Shared fixtures and the no-live-model guard.

Per _design/00-testing-without-a-model.md: ALLOW_MODEL_REQUESTS is hard-off, so a
stray call to a real provider fails the suite instead of silently billing anyone.
Tiers 0-2 cannot spend money; they are not merely expected not to.
"""

from pydantic_ai import models

models.ALLOW_MODEL_REQUESTS = False
