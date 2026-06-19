"""Baseline smoke test so the TDD loop starts from green.

Real feature tests are added per-issue during the TDD phase.
"""

import inshust


def test_package_exposes_main_entry():
    assert callable(inshust.main)
