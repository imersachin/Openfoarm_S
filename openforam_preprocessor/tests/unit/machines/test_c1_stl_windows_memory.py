"""C1 review: an STL machine gets the Windows-memory WARNING before meshing,
still meshes, and writes the same mesh files as with no reading."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from machines.config import MachineProjectConfig
from machines.msh_import.host_memory import HostMemory
from machines.pipeline import MachinePipeline
from tests.fakes import PLENTY, openfoam_env
from tests.fakes_machines import FakeMachineRunner
from tests.fixtures.machines import configs
from tests.unit.machines.test_machine_pipeline import MACHINE_EXECUTABLES

GB = 10**9


def mesh(tmp_path: Path, name: str, host: HostMemory | None) -> tuple[Any, dict[str, str]]:
    root = tmp_path / name
    pipeline = MachinePipeline(FakeMachineRunner(),
                               environment=openfoam_env(tmp_path / f"env_{name}", "v2512",
                                                        MACHINE_EXECUTABLES),
                               system_probe=lambda _: PLENTY, host_probe=lambda: host)
    data = configs.hawt(tmp_path / "geometry")
    result = pipeline.run_sync(root, MachineProjectConfig.model_validate(data))
    files = {str(p.relative_to(root / "cases")): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted((root / "cases").rglob("*")) if p.is_file()}
    return result, files


def test_stl_meshing_warns_and_writes_the_same_mesh(tmp_path: Path) -> None:
    low = HostMemory(64 * GB, 60 * GB, free_bytes=1 * GB)
    warned, warned_files = mesh(tmp_path, "low", low)
    plain, plain_files = mesh(tmp_path, "none", None)
    assert warned.succeeded and plain.succeeded
    found = [i for i in warned.issues if i.code == "WINDOWS_MEMORY_LOW"]
    assert len(found) == 1 and found[0].severity.value == "WARNING"
    assert found[0].stage.value == "RESOURCE_PREFLIGHT"
    assert not [i for i in plain.issues if i.code == "WINDOWS_MEMORY_LOW"]
    assert warned_files and warned_files == plain_files  # byte-identical case files
