"""Tests never read the real Windows host (C1 review): the default probe of
MachinePipeline returns None (unreadable), so a test's issues and reports do
not depend on what else runs on the machine. A test that needs a host value
sets pipeline.host_probe itself."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def no_real_host_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("machines.pipeline.host_memory", lambda: None)
