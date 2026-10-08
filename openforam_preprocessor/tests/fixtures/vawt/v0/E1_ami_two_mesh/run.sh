#!/bin/bash
# E1 - AMI from two meshes (spec section 10, first candidate):
#   outer:  blockMesh -> snappyHexMesh (cylinder removed)
#   rotor:  blockMesh -> surfaceFeatureExtract -> snappyHexMesh -> topoSet (cellZone)
#   merged: mergeMeshes -> createPatch (cyclicAMI pair) -> checkMesh -> foamMeshToFluent
# Run from anywhere: bash tests/fixtures/vawt/v0/E1_ami_two_mesh/run.sh
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../common/lib.sh"
W=$WORK_ROOT/E1
LOGS=$HERE/logs
rm -rf "$LOGS" "$W"; mkdir -p "$LOGS" "$W"

new_case "$HERE/outer" "$W/outer"
new_case "$HERE/rotor" "$W/rotor"
geometry "$W/rotor"

outer_ok=0; rotor_ok=0
step "$LOGS" outer_01_blockMesh      blockMesh -case "$W/outer" &&
step "$LOGS" outer_02_snappyHexMesh  snappyHexMesh -case "$W/outer" -overwrite && outer_ok=1
# checkMesh exits 1 when it finds failed checks; recorded, not a gate.
[ $outer_ok = 1 ] && step "$LOGS" outer_03_checkMesh checkMesh -case "$W/outer" -allGeometry -allTopology -meshQuality

step "$LOGS" rotor_01_blockMesh      blockMesh -case "$W/rotor" &&
step "$LOGS" rotor_02_surfaceFeatureExtract surfaceFeatureExtract -case "$W/rotor" &&
step "$LOGS" rotor_03_snappyHexMesh  snappyHexMesh -case "$W/rotor" -overwrite &&
step "$LOGS" rotor_04_topoSet        topoSet -case "$W/rotor" && rotor_ok=1
[ $rotor_ok = 1 ] && step "$LOGS" rotor_05_checkMesh checkMesh -case "$W/rotor" -allGeometry -allTopology -meshQuality

# The merged case starts as a copy of the outer case (mergeMeshes writes into the master).
rm -rf "$W/merged"; cp -r "$W/outer" "$W/merged"
cp "$HERE/merged/system/"* "$W/merged/system/"
[ $outer_ok = 1 ] && [ $rotor_ok = 1 ] &&
step "$LOGS" merged_01_mergeMeshes   mergeMeshes -overwrite "$W/merged" "$W/rotor" &&
step "$LOGS" merged_02_createPatch   createPatch -case "$W/merged" -overwrite &&
step "$LOGS" merged_03_checkMesh     checkMesh -case "$W/merged" -allGeometry -allTopology -meshQuality &&
step "$LOGS" merged_04_foamMeshToFluent foamMeshToFluent -case "$W/merged"

# Which checkMesh option produces the failed check? (the engine runs all three)
if [ -d "$W/merged/constant/polyMesh" ]; then
    step "$LOGS" merged_flags_none         checkMesh -case "$W/merged"
    step "$LOGS" merged_flags_meshQuality  checkMesh -case "$W/merged" -meshQuality
    step "$LOGS" merged_flags_allTopology  checkMesh -case "$W/merged" -allTopology
    step "$LOGS" merged_flags_allGeometry  checkMesh -case "$W/merged" -allGeometry
fi

# Evidence that is small enough to keep.
for c in outer rotor merged; do
    [ -f "$W/$c/constant/polyMesh/boundary" ] && cp "$W/$c/constant/polyMesh/boundary" "$LOGS/${c}_boundary"
    [ -f "$W/$c/constant/polyMesh/cellZones" ] && head -c 2000 "$W/$c/constant/polyMesh/cellZones" > "$LOGS/${c}_cellZones.head"
done
find "$W/merged" -name '*.msh' -printf '%P %s\n' > "$LOGS/merged_msh_files.txt"
# The .msh is too large to keep: record its zone sections (39 = zone names/types,
# 12 = cell zones, 13 = face zones) as evidence.
for m in $(find "$W/merged" -name '*.msh'); do
    grep -aE '^\((39|12|13) \(' "$m" > "$LOGS/merged_msh_zones.txt"
done
keep_logs "$LOGS"
cat "$LOGS/exit_codes.txt"
