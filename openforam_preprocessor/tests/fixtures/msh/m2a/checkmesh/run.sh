#!/bin/bash
# M2a evidence 1: checkMesh's wording for each defect class, with the flags the
# import runs (machines.msh_import.cases: -allTopology -meshQuality -writeSets vtk
# -writeFields). A 4 x 4 x 4 blockMesh cube, then one defect each (defect.py);
# the logs are the fixtures of machines.msh_import.quality's tests.
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../../e0/common/lib.sh"
W=$WORK_ROOT/M2a_checkmesh
LOGS=$HERE/logs
rm -rf "$LOGS" "$W"; mkdir -p "$LOGS" "$W"

new_case "$W/base"
cat > "$W/base/system/blockMeshDict" <<'D'
FoamFile { version 2.0; format ascii; class dictionary; object blockMeshDict; }
vertices ((0 0 0) (1 0 0) (1 1 0) (0 1 0) (0 0 1) (1 0 1) (1 1 1) (0 1 1));
blocks (hex (0 1 2 3 4 5 6 7) (4 4 4) simpleGrading (1 1 1));
boundary ( walls { type wall; faces ((0 4 7 3) (1 2 6 5) (0 1 5 4) (3 7 6 2) (0 3 2 1) (4 5 6 7)); } );
D
step "$LOGS" base_blockMesh blockMesh -case "$W/base"

for kind in ok negvol pushpoint flipface openboundary nonortho aspect; do
    rm -rf "$W/$kind"; cp -r "$W/base" "$W/$kind"
    [ "$kind" = ok ] || py "$HERE/defect.py" "$W/$kind/constant/polyMesh" "$kind"
    step "$LOGS" "${kind}_checkMesh" checkMesh -case "$W/$kind" -allTopology -meshQuality \
        -writeSets vtk -writeFields "(nonOrthoAngle skewness faceWeight aspectRatio)"
done
# The second quality pass (machines.msh_import.quality.ZERO_QUALITY_DICT): tet
# quality and determinant at or below 0, every other check off.
for kind in aspect pushpoint; do
    z="$W/${kind}_zero"; rm -rf "$z"; cp -r "$W/$kind" "$z"; rm -rf "$z/constant/polyMesh/sets"
    py -c "import sys; sys.path.insert(0, '$HERE/../../../../..'); from machines.msh_import.quality import ZERO_QUALITY_DICT as d; open(sys.argv[1], 'w').write(d)" "$z/system/meshQualityDict"
    step "$LOGS" "${kind}_zero_checkMesh" checkMesh -case "$z" -meshQuality
done
keep_logs "$LOGS"
cat "$LOGS/exit_codes.txt"
