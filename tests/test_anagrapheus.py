"""Basic tests for anagrapheus."""

import anagrapheus


def test_import():
    assert hasattr(anagrapheus, "__all__")
