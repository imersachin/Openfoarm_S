#!/bin/bash
# R5 - VAWT pole crossing the rotating zone (owner decision 1: prove both):
#   rotating: the pole is split by the zone; its part inside rotates (pole_rot).
#             Solved twice: pole_rot moving, and pole_rot held stationary.
#   hole:     annular zone (r 0.08-0.78); the pole and the fluid around it
#             stay stationary; two AMI pairs (outside and hole).
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../common/lib.sh"
W=$WORK_ROOT/R5
LOGS=$HERE/logs
rm -rf "$LOGS" "$W"; mkdir -p "$LOGS" "$W"

for v in rotating hole; do
    o=$W/${v}_outer
    new_case "$HERE/$v/outer" "$o"
    geometry pole "$o"
    step "$LOGS" "${v}_outer_01_blockMesh" blockMesh -case "$o" || continue
    if [ "$v" = hole ]; then
        step "$LOGS" hole_outer_02_surfaceFeatureExtract surfaceFeatureExtract -case "$o" || continue
    fi
    step "$LOGS" "${v}_outer_03_snappyHexMesh" snappyHexMesh -case "$o" -overwrite &&
    step "$LOGS" "${v}_outer_04_checkMesh" checkMesh -case "$o" -allTopology -meshQuality

    r=$W/${v}_rotor
    new_case "$HERE/$v/rotor" "$r"
    geometry pole "$r"
    step "$LOGS" "${v}_rotor_01_blockMesh" blockMesh -case "$r" &&
    step "$LOGS" "${v}_rotor_02_surfaceFeatureExtract" surfaceFeatureExtract -case "$r" &&
    step "$LOGS" "${v}_rotor_03_snappyHexMesh" snappyHexMesh -case "$r" -overwrite &&
    step "$LOGS" "${v}_rotor_04_topoSet" topoSet -case "$r" &&
    step "$LOGS" "${v}_rotor_05_checkMesh" checkMesh -case "$r" -allTopology -meshQuality

    m=$W/${v}_merged
    rm -rf "$m"; cp -r "$o" "$m"; cp "$HERE/$v/merged/system/"* "$m/system/"
    step "$LOGS" "${v}_merged_01_mergeMeshes" in_case "$m" mergeMeshes -overwrite "$m" "$r" &&
    step "$LOGS" "${v}_merged_02_createPatch" createPatch -case "$m" -overwrite &&
    step "$LOGS" "${v}_merged_03_checkMesh" checkMesh -case "$m" -allTopology -meshQuality
    for c in outer rotor merged; do keep_mesh_summary "$LOGS" "${v}_$c" "$W/${v}_$c"; done
done

solve "$LOGS" solve_rotating "$W/rotating_merged" "$HERE/solve_rotating" "$HERE/solve_rotating/0.sst" sst "$W/solve_rotating"
solve "$LOGS" solve_rotating_static "$W/rotating_merged" "$HERE/solve_rotating_static" "$HERE/solve_rotating_static/0.sst" sst "$W/solve_rotating_static"
solve "$LOGS" solve_hole "$W/hole_merged" "$HERE/solve_hole" "$HERE/solve_hole/0.sst" sst "$W/solve_hole"
keep_logs "$LOGS"
cat "$LOGS/exit_codes.txt"
