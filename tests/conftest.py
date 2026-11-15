"""Shared test configuration.

Hypothesis profiles: "default" for local runs and "ci" for more examples in
the pipeline. Choose one with the HYPOTHESIS_PROFILE environment variable.
"""

import os

from hypothesis import HealthCheck, settings

# Reconciling a generated Head of Ops case takes a few milliseconds but can
# spike on a loaded machine, so the per-example deadline is disabled rather
# than letting timing noise fail a correctness property.
settings.register_profile(
    "default", max_examples=100, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
settings.register_profile(
    "ci", max_examples=500, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
