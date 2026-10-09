#!/bin/bash
# R1 - cylinder domain (spec section 5.2), no body:
#   (a) O-grid: blockMesh only (centre block + four blocks with arc edges)
#   (b) box background cut to the cylinder by snappyHexMesh (STL with regions)
# Run from anywhere: bash tests/fixtures/machines/g0/R1_cylinder_domain/run.sh
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../common/lib.sh"
W=$WORK_ROOT/R1
LOGS=$HERE/logs
rm -rf "$LOGS" "$W"; mkdir -p "$LOGS" "$W"

new_case "$HERE/ogrid" "$W/ogrid"
step "$LOGS" ogrid_01_blockMesh  blockMesh -case "$W/ogrid" &&
step "$LOGS" ogrid_02_checkMesh  checkMesh -case "$W/ogrid" -allTopology -meshQuality
keep_mesh_summary "$LOGS" ogrid "$W/ogrid"

new_case "$HERE/snappy" "$W/snappy"
geometry hawt "$W/snappy"
step "$LOGS" snappy_01_blockMesh  blockMesh -case "$W/snappy" &&
step "$LOGS" snappy_02_surfaceFeatureExtract surfaceFeatureExtract -case "$W/snappy" &&
step "$LOGS" snappy_03_snappyHexMesh snappyHexMesh -case "$W/snappy" -overwrite &&
step "$LOGS" snappy_04_checkMesh  checkMesh -case "$W/snappy" -allTopology -meshQuality
keep_mesh_summary "$LOGS" snappy "$W/snappy"
keep_logs "$LOGS"
cat "$LOGS/exit_codes.txt"
