"""Shared fixtures.

Every test runs against an isolated SOURCING_HOME so a test run can never read
or overwrite a real buyer's saved profile.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("SOURCING_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("SOURCING_PROVIDER", "mock")
    from sourcing1688 import config

    config.get_settings(refresh=True)
    yield
    config.get_settings(refresh=True)


@pytest.fixture
def profile():
    from sourcing1688.preferences import default_profile

    return default_profile()


@pytest.fixture
def provider():
    from sourcing1688.providers import get_provider

    return get_provider("mock")
