#!/bin/bash
# R3 - HAWT, axial flow, disc rotating zone (V0 method A: two meshes + AMI):
#   outer_ogrid / outer_snappy: the R1 domain methods with the zone removed
#   rotor: blockMesh -> surfaceFeatureExtract -> snappyHexMesh -> topoSet
#   merged_<outer>: mergeMeshes -> createPatch -> checkMesh
#   solve: a few pimpleFoam steps (k-omega SST on both; laminar on the O-grid)
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../common/lib.sh"
W=$WORK_ROOT/R3
LOGS=$HERE/logs
rm -rf "$LOGS" "$W"; mkdir -p "$LOGS" "$W"

new_case "$HERE/outer_ogrid" "$W/outer_ogrid"
step "$LOGS" outer_ogrid_01_blockMesh blockMesh -case "$W/outer_ogrid" &&
step "$LOGS" outer_ogrid_02_snappyHexMesh snappyHexMesh -case "$W/outer_ogrid" -overwrite &&
step "$LOGS" outer_ogrid_03_checkMesh checkMesh -case "$W/outer_ogrid" -allTopology -meshQuality

new_case "$HERE/outer_snappy" "$W/outer_snappy"
geometry hawt "$W/outer_snappy"
step "$LOGS" outer_snappy_01_blockMesh blockMesh -case "$W/outer_snappy" &&
step "$LOGS" outer_snappy_02_surfaceFeatureExtract surfaceFeatureExtract -case "$W/outer_snappy" &&
step "$LOGS" outer_snappy_03_snappyHexMesh snappyHexMesh -case "$W/outer_snappy" -overwrite &&
step "$LOGS" outer_snappy_04_checkMesh checkMesh -case "$W/outer_snappy" -allTopology -meshQuality

new_case "$HERE/rotor" "$W/rotor"
geometry hawt "$W/rotor"
step "$LOGS" rotor_01_blockMesh blockMesh -case "$W/rotor" &&
step "$LOGS" rotor_02_surfaceFeatureExtract surfaceFeatureExtract -case "$W/rotor" &&
step "$LOGS" rotor_03_snappyHexMesh snappyHexMesh -case "$W/rotor" -overwrite &&
step "$LOGS" rotor_04_topoSet topoSet -case "$W/rotor" &&
step "$LOGS" rotor_05_checkMesh checkMesh -case "$W/rotor" -allTopology -meshQuality
keep_mesh_summary "$LOGS" rotor "$W/rotor"

for o in ogrid snappy; do
    m=$W/merged_$o
    rm -rf "$m"; cp -r "$W/outer_$o" "$m"; cp "$HERE/merged/system/"* "$m/system/"
    step "$LOGS" "merged_${o}_01_mergeMeshes" in_case "$m" mergeMeshes -overwrite "$m" "$W/rotor" &&
    step "$LOGS" "merged_${o}_02_createPatch" createPatch -case "$m" -overwrite &&
    step "$LOGS" "merged_${o}_03_checkMesh" checkMesh -case "$m" -allTopology -meshQuality
    keep_mesh_summary "$LOGS" "merged_$o" "$m"
done

solve "$LOGS" solve_ogrid_sst "$W/merged_ogrid" "$HERE/solve" "$HERE/solve/0.sst" sst "$W/solve_ogrid_sst"
solve "$LOGS" solve_snappy_sst "$W/merged_snappy" "$HERE/solve" "$HERE/solve/0.sst" sst "$W/solve_snappy_sst"
solve "$LOGS" solve_ogrid_laminar "$W/merged_ogrid" "$HERE/solve" "$HERE/solve/0.laminar" laminar "$W/solve_ogrid_laminar"
keep_logs "$LOGS"
cat "$LOGS/exit_codes.txt"
