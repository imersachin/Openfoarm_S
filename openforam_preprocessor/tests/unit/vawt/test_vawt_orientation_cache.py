"""The per-rotor cache of the blade-orientation check: identical validation
with and without it, and a changed rotor or check version always recomputes."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import trimesh

from geometry.metrics import body_orientations
from tests.fixtures.vawt.drafts import preset_draft
from tests.fixtures.vawt.rotor import rotor_mesh
from vawt import validation
from vawt.config import VawtProjectConfig
from vawt.rotor_metrics import compute_rotor_metrics
from vawt.validation import (
    ValidationThresholds,
    cached_body_orientations,
    check_config,
    clear_orientation_cache,
    load_rotor,
)


@pytest.fixture(autouse=True)
def empty_cache() -> Iterator[None]:
    clear_orientation_cache()
    yield
    clear_orientation_cache()


@pytest.fixture
def computed(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Counts real computations of the check (cache misses)."""
    calls: list[int] = []

    def counting(mesh: trimesh.Trimesh) -> Any:
        calls.append(len(mesh.faces))
        return body_orientations(mesh)

    monkeypatch.setattr(validation, "body_orientations", counting)
    return calls


def issues(tmp_path: Path, invert_body: int | None) -> tuple[Any, ...]:
    config = VawtProjectConfig.model_validate(preset_draft(tmp_path, invert_body=invert_body))
    mesh, _ = load_rotor(config)
    assert mesh is not None
    metrics = compute_rotor_metrics(mesh.vertices, config.rotor.axis)
    return check_config(config, mesh, metrics, ValidationThresholds())


@pytest.mark.parametrize("invert_body", [None, 1])
def test_validation_is_identical_with_and_without_the_cache(tmp_path: Path,
                                                            invert_body: int | None) -> None:
    uncached = issues(tmp_path, invert_body)  # empty cache: computed
    cached = issues(tmp_path, invert_body)  # same rotor: from the cache

    assert cached == uncached
    codes = {i.code for i in cached}
    assert ("BODY_INSIDE_OUT" in codes) is (invert_body is not None)


def test_the_cached_result_equals_the_check(computed: list[int]) -> None:
    mesh = rotor_mesh(invert_body=2)

    first = cached_body_orientations(mesh)
    second = cached_body_orientations(mesh.copy())  # same geometry, another object

    assert first == second == body_orientations(mesh)
    assert len(computed) == 1


@pytest.mark.parametrize("change", ["inverted_body", "moved", "scaled", "one_vertex"])
def test_a_changed_rotor_recomputes(computed: list[int], change: str) -> None:
    mesh = rotor_mesh()
    before = cached_body_orientations(mesh)
    if change == "inverted_body":
        changed = rotor_mesh(invert_body=1)
    elif change == "moved":
        changed = mesh.copy()
        changed.apply_translation([0.0, 0.0, 1e-3])  # trimesh skips near-identity moves
    elif change == "scaled":
        changed = rotor_mesh(scale=2.0)
    else:
        changed = mesh.copy()
        vertices = changed.vertices.copy()
        vertices[0, 0] += 1e-12
        changed.vertices = vertices

    after = cached_body_orientations(changed)

    assert len(computed) == 2
    assert after == body_orientations(changed)
    if change == "inverted_body":
        assert after != before and any(b.inside_out for b in after)


def test_a_new_check_version_recomputes(computed: list[int],
                                        monkeypatch: pytest.MonkeyPatch) -> None:
    mesh = rotor_mesh()
    cached_body_orientations(mesh)

    monkeypatch.setattr(validation, "BODY_ORIENTATION_VERSION",
                        validation.BODY_ORIENTATION_VERSION + 1)
    cached_body_orientations(mesh)

    assert len(computed) == 2
