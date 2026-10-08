"""V3 end to end on real OpenFOAM v2512: the fixture rotor, AMI, through VawtPipeline.

Skipped unless the OpenFOAM tools are on PATH. Run explicitly with:

    pytest -m openfoam tests/integration/test_vawt_pipeline_real.py -v
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from openfoam.runner import CommandResult, OpenFOAMRunner
from tests.fakes_vawt import VAWT_TOOLS
from tests.fixtures.vawt.drafts import preset_draft
from vawt.config import VawtProjectConfig
from vawt.operations import VawtOperation
from vawt.pipeline import VawtPipeline
from vawt.status import read_status

pytestmark = [
    pytest.mark.openfoam,
    pytest.mark.skipif(any(shutil.which(t) is None for t in VAWT_TOOLS),
                       reason="OpenFOAM (openfoam.com) environment not sourced"),
]


class CountingRunner(OpenFOAMRunner):
    """The real runner, recording which commands it started."""

    def __init__(self) -> None:
        super().__init__()
        self.started: list[str] = []

    async def run(self, argv: Sequence[str], **kwargs: Any) -> CommandResult:
        self.started.append(argv[0])
        return await super().run(argv, **kwargs)


def test_ami_pipeline_end_to_end(tmp_path: Path) -> None:
    draft = preset_draft(tmp_path)  # SIMPLE preset for the fixture rotor, AMI
    draft["refinement"]["extract_features"] = True
    config = VawtProjectConfig.model_validate(draft)
    root = tmp_path / "project"

    runner = CountingRunner()
    result = VawtPipeline(runner).run_sync(root, config)

    assert result.succeeded, [(i.code, i.message) for i in result.issues]
    assert runner.started == ["blockMesh", "snappyHexMesh", "surfaceFeatureExtract",
                              "blockMesh", "snappyHexMesh", "topoSet", "mergeMeshes",
                              "createPatch", "checkMesh"]
    report = json.loads((root / "reports/mesh_quality_report.json").read_text("utf-8"))
    assert report["assessment"]["mesh_validity"] == "VALID"
    assert report["regions"] == {"expected": 2, "found": 2}
    faces = {p["name"]: p["faces"] for p in report["metrics"]["patches"]}
    assert faces["AMI1"] > 0 and faces["AMI2"] > 0 and faces["rotor"] > 0
    assert "rotating" in (root / "cases/merged/constant/polyMesh/cellZones").read_text(
        "utf-8", errors="replace")
    assert read_status(root)["state"] == "SUCCEEDED"  # type: ignore[index]

    # Nothing changed: no OpenFOAM command starts (spec 14.2).
    again_runner = CountingRunner()
    again = VawtPipeline(again_runner).run_sync(root, config)
    assert again.succeeded and again_runner.started == []
    assert {VawtOperation.OUTER_MESH, VawtOperation.ROTOR_MESH, VawtOperation.ASSEMBLE,
            VawtOperation.CHECK_MESH} <= set(again.reused)
