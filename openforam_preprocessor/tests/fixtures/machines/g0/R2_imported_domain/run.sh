#!/bin/bash
# R2 - imported domain (spec section 5.3), a closed duct turned 20 deg:
#   named:    one ASCII STL with named regions
#   per_file: one STL per patch
#   binary:   the same surface as binary STL (region names cannot be stored)
#   open:     per-file set without the outlet (union not closed)
# Plus check_regions.py: what a Python reader sees in each input.
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../common/lib.sh"
W=$WORK_ROOT/R2
LOGS=$HERE/logs
rm -rf "$LOGS" "$W"; mkdir -p "$LOGS" "$W"

for v in named per_file binary open; do
    new_case "$HERE/$v" "$W/$v"
    geometry duct "$W/$v"
    step "$LOGS" "${v}_01_blockMesh" blockMesh -case "$W/$v" &&
    step "$LOGS" "${v}_02_surfaceFeatureExtract" surfaceFeatureExtract -case "$W/$v" &&
    step "$LOGS" "${v}_03_snappyHexMesh" snappyHexMesh -case "$W/$v" -overwrite &&
    step "$LOGS" "${v}_04_checkMesh" checkMesh -case "$W/$v" -allTopology -meshQuality
    keep_mesh_summary "$LOGS" "$v" "$W/$v"
done
(cd "$REPO_ROOT" && "$PYTHON" -m tests.fixtures.machines.g0.R2_imported_domain.check_regions \
    "$W/named/constant/triSurface") > "$LOGS/python_checks.json"
keep_logs "$LOGS"
cat "$LOGS/exit_codes.txt"
