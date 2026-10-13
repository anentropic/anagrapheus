"""
Shared fixtures, the no-live-model guard, and the Hypothesis profiles.

ALLOW_MODEL_REQUESTS is switched off for the whole suite, so a stray call to a real
provider fails the test instead of silently billing anyone. The ordinary tests cannot
spend money; they are not merely expected not to. Only the tier-3 live smoke tests lift
the guard, and only for themselves.

How many examples each property test generates is set by a profile, chosen with the
``HYPOTHESIS_PROFILE`` environment variable: ``ci`` (the default, what CI runs), ``dev``
for a quick local pass, and ``deep`` for a long hunt before a release or after touching
the identity or re-key logic.
"""

import os

from hypothesis import settings
from pydantic_ai import models

models.ALLOW_MODEL_REQUESTS = False

# ``ci`` is the most any property test ran before the counts moved here, so no test
# runs fewer examples by default than it used to.
settings.register_profile("ci", max_examples=400)
settings.register_profile("dev", max_examples=25)
settings.register_profile("deep", max_examples=2000)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))
