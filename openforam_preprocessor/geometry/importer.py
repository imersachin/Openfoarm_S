from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import trimesh

from core.issues import Issue, IssueCategory, IssueSeverity, IssueStage


@dataclass(frozen=True)
class ImportResult:
    """Raw STL triangles exactly as stored in the file (no processing/repair)."""

    source: Path
    mesh: trimesh.Trimesh | None
    issues: tuple[Issue, ...]


def _import_issue(code: str, message: str, source: Path, explanation: str, action: str,
                  details: dict[str, Any] | None = None) -> Issue:
    return Issue(
        category=IssueCategory.INPUT,
        severity=IssueSeverity.ERROR,
        stage=IssueStage.GEOMETRY_IMPORT,
        code=code,
        message=message,
        explanation=explanation,
        suggested_action=action,
        artifact_reference=str(source),
        details=details or {},
    )


def import_stl(source: Path) -> ImportResult:
    if not source.is_file():
        return ImportResult(source, None, (_import_issue(
            "GEOMETRY_SOURCE_MISSING",
            f"Geometry file not found: {source}",
            source,
            "The configured STL path does not point to an existing file.",
            "Check the geometry source path in the project settings.",
        ),))

    try:
        loaded = trimesh.load_mesh(source, process=False)
        if isinstance(loaded, trimesh.Scene):
            loaded = trimesh.util.concatenate(tuple(loaded.geometry.values()))
    except Exception as exc:  # trimesh raises many unrelated types for bad input
        return ImportResult(source, None, (_import_issue(
            "GEOMETRY_UNREADABLE",
            f"Geometry file could not be parsed as STL: {source}",
            source,
            "The file is corrupt, truncated, or not a valid STL surface.",
            "Re-export the geometry as ASCII or binary STL.",
            {"exception_type": type(exc).__name__, "exception": str(exc)},
        ),))

    if not isinstance(loaded, trimesh.Trimesh):
        return ImportResult(source, None, (_import_issue(
            "GEOMETRY_UNSUPPORTED_OBJECT",
            f"Unsupported geometry object read from {source}",
            source,
            "The file did not contain a triangulated surface.",
            "Provide a triangulated STL surface.",
            {"object_type": type(loaded).__name__},
        ),))

    return ImportResult(source, loaded, ())
