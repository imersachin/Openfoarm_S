#!/bin/bash
# E2 - single snappyHexMesh pass with the cylinder as a zoned surface
# (spec section 10 alternative; v2512 template inflowOutflowRotating).
#   case: blockMesh -> surfaceFeatureExtract -> snappyHexMesh -> checkMesh
#   E2a CELL_ZONE: the snapped mesh as is (cellZone + internal faceZone) -> foamMeshToFluent
#   E2b AMI:       copy -> createBaffles (cyclicAMI pair from the faceZone) -> checkMesh
#                  -> foamMeshToFluent
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../common/lib.sh"
W=$WORK_ROOT/E2
LOGS=$HERE/logs
rm -rf "$LOGS" "$W"; mkdir -p "$LOGS" "$W"

new_case "$HERE/case" "$W/cellzone"
# Same background and feature dictionaries as E1 (outer domain / rotor features).
cp "$HERE/../E1_ami_two_mesh/outer/system/blockMeshDict" "$W/cellzone/system/"
cp "$HERE/../E1_ami_two_mesh/rotor/system/surfaceFeatureExtractDict" "$W/cellzone/system/"
geometry "$W/cellzone"

ok=0
step "$LOGS" 01_blockMesh             blockMesh -case "$W/cellzone" &&
step "$LOGS" 02_surfaceFeatureExtract surfaceFeatureExtract -case "$W/cellzone" &&
step "$LOGS" 03_snappyHexMesh         snappyHexMesh -case "$W/cellzone" -overwrite && ok=1

if [ $ok = 1 ]; then
    cp -r "$W/cellzone" "$W/ami"
    cp "$HERE/ami/system/"* "$W/ami/system/"
    step "$LOGS" E2a_04_checkMesh        checkMesh -case "$W/cellzone" -allGeometry -allTopology -meshQuality
    step "$LOGS" E2a_05_foamMeshToFluent foamMeshToFluent -case "$W/cellzone"
    step "$LOGS" E2b_04_createBaffles    createBaffles -case "$W/ami" -overwrite &&
    step "$LOGS" E2b_05_checkMesh        checkMesh -case "$W/ami" -allGeometry -allTopology -meshQuality &&
    step "$LOGS" E2b_06_foamMeshToFluent foamMeshToFluent -case "$W/ami"
    # Same comparison as E1: checkMesh without -allGeometry.
    step "$LOGS" E2a_flags_none checkMesh -case "$W/cellzone"
    step "$LOGS" E2b_flags_none checkMesh -case "$W/ami"
fi

for c in cellzone ami; do
    p=$W/$c/constant/polyMesh
    [ -f "$p/boundary" ] && cp "$p/boundary" "$LOGS/${c}_boundary"
    for z in cellZones faceZones; do
        [ -f "$p/$z" ] && head -c 2000 "$p/$z" > "$LOGS/${c}_${z}.head"
    done
    for m in $(find "$W/$c" -name '*.msh'); do
        printf '%s %s\n' "${m#$W/}" "$(stat -c %s "$m")" >> "$LOGS/msh_files.txt"
        grep -aE '^\((39|12|13) \(' "$m" > "$LOGS/${c}_msh_zones.txt"
    done
done
keep_logs "$LOGS"
cat "$LOGS/exit_codes.txt"
