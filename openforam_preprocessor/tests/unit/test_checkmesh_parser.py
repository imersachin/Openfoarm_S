from mesh.parser import CheckMeshParser


def test_parse_core_checkmesh_metrics() -> None:
    log = """
    points:           12500
    faces:            65000
    cells:            21000
    Mesh non-orthogonality Max: 52.4 average: 8.1
    Mesh OK.
    """

    metrics = CheckMeshParser().parse_text(log)

    assert metrics.points == 12500
    assert metrics.faces == 65000
    assert metrics.cells == 21000
    assert metrics.max_non_orthogonality == 52.4
    assert metrics.average_non_orthogonality == 8.1
    assert metrics.mesh_ok is True
