#!/bin/bash
# E3 - Wall baffles through Fluent (plan E0.4). An approximation: real Fluent
# wall / wall-shadow pairs are not available (owner, answer 2).
#   src: the E2 box -> topoSet (face zone at x 0.5, y < 0.25) -> createBaffles
#        (walls plate and plate-shadow on the same faces) -> checkMesh
#        -> mergeOrSplitBaffles -detectOnly -> foamMeshToFluent
#   f3d / f2d: fluent3DMeshToFoam / fluentMeshToFoam -> checkMesh
#        -> mergeOrSplitBaffles -detectOnly
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../common/lib.sh"
W=$WORK_ROOT/E3
LOGS=$HERE/logs
rm -rf "$LOGS" "$W"; mkdir -p "$LOGS" "$W"

new_case "$W/src" "$HERE/src"
cp "$E0_DIR/E2_fluent/src/system/blockMeshDict" "$W/src/system/"
step "$LOGS" src_01_blockMesh blockMesh -case "$W/src" &&
step "$LOGS" src_02_topoSet topoSet -case "$W/src" &&
step "$LOGS" src_03_createBaffles createBaffles -case "$W/src" -overwrite &&
step "$LOGS" src_04_checkMesh checkMesh -case "$W/src" -allTopology -meshQuality
step "$LOGS" src_05_detectBaffles mergeOrSplitBaffles -case "$W/src" -detectOnly
step "$LOGS" src_06_foamMeshToFluent foamMeshToFluent -case "$W/src"
keep_mesh_summary "$LOGS" src "$W/src"
MSH=$W/src/fluentInterface/src.msh
head -c 1500 "$MSH" > "$LOGS/src_msh.head"
grep -aE '^\((39|45) ' "$MSH" > "$LOGS/src_msh_zones.txt"

for c in f3d f2d; do
    tool=fluent3DMeshToFoam; [ $c = f2d ] && tool=fluentMeshToFoam
    new_case "$W/$c"
    step "$LOGS" "${c}_01_$tool" $tool "$MSH" -case "$W/$c" &&
    step "$LOGS" "${c}_02_checkMesh" checkMesh -case "$W/$c" -allTopology -meshQuality
    step "$LOGS" "${c}_03_detectBaffles" mergeOrSplitBaffles -case "$W/$c" -detectOnly
    keep_mesh_summary "$LOGS" "$c" "$W/$c"
done
keep_logs "$LOGS"
cat "$LOGS/exit_codes.txt"
