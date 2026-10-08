#!/bin/bash
# E4 - exit codes and output of each command on typical failures (spec section 10 Q6).
# Success logs for every command are in E1/E2. Each failure here is one small case
# derived from the E1 rotor sub-case.
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../common/lib.sh"
W=$WORK_ROOT/E4
LOGS=$HERE/logs
ROTOR=$HERE/../E1_ami_two_mesh/rotor
rm -rf "$LOGS" "$W"; mkdir -p "$LOGS" "$W"

set_point() {  # set_point <case> "(x y z)"
    foamDictionary "$1/system/snappyHexMeshDict" \
        -entry castellatedMeshControls/locationInMesh -set "$2" >/dev/null
}
prepared() {  # prepared <case> : rotor case with background mesh and features
    new_case "$ROTOR" "$1"; geometry "$1"
    blockMesh -case "$1" >/dev/null 2>&1
    surfaceFeatureExtract -case "$1" >/dev/null 2>&1
}

# 1. snappyHexMesh: mesh point exactly on a background-cell edge (V1 preset point).
prepared "$W/location_on_cell_edge"
set_point "$W/location_on_cell_edge" "(0.65 0 0)"
step "$LOGS" location_on_cell_edge snappyHexMesh -case "$W/location_on_cell_edge" -overwrite

# 2. snappyHexMesh: mesh point inside a blade (rotor solid), off cell faces.
prepared "$W/location_in_rotor"
set_point "$W/location_in_rotor" "(0.5 0.0113 0.0137)"
foamDictionary "$W/location_in_rotor/system/snappyHexMeshDict" -entry addLayers -set false >/dev/null
step "$LOGS" location_in_rotor snappyHexMesh -case "$W/location_in_rotor" -overwrite
step "$LOGS" location_in_rotor_checkMesh checkMesh -case "$W/location_in_rotor"

# 3. blockMesh: system/blockMeshDict missing.
new_case "$ROTOR" "$W/no_blockMeshDict"; rm "$W/no_blockMeshDict/system/blockMeshDict"
step "$LOGS" blockMesh_missing_dict blockMesh -case "$W/no_blockMeshDict"

# 4. surfaceFeatureExtract: the STL named in the dictionary is missing.
new_case "$ROTOR" "$W/no_stl"
step "$LOGS" surfaceFeatureExtract_missing_stl surfaceFeatureExtract -case "$W/no_stl"

# 5. snappyHexMesh: the .eMesh it references was never extracted.
new_case "$ROTOR" "$W/no_emesh"; geometry "$W/no_emesh"
blockMesh -case "$W/no_emesh" >/dev/null 2>&1
step "$LOGS" snappyHexMesh_missing_eMesh snappyHexMesh -case "$W/no_emesh" -overwrite

# 6. createPatch: source patch does not exist (background mesh only).
new_case "$ROTOR" "$W/bad_patch"; blockMesh -case "$W/bad_patch" >/dev/null 2>&1
cp "$HERE/../E1_ami_two_mesh/merged/system/createPatchDict" "$W/bad_patch/system/"
step "$LOGS" createPatch_missing_source_patch createPatch -case "$W/bad_patch" -overwrite

# 7. createBaffles: faceZone does not exist.
new_case "$ROTOR" "$W/bad_zone"; blockMesh -case "$W/bad_zone" >/dev/null 2>&1
cp "$HERE/../E2_single_mesh/ami/system/createBafflesDict" "$W/bad_zone/system/"
step "$LOGS" createBaffles_missing_faceZone createBaffles -case "$W/bad_zone" -overwrite

# 8. mergeMeshes: the added case has no mesh.
new_case "$ROTOR" "$W/merge_master"; blockMesh -case "$W/merge_master" >/dev/null 2>&1
new_case "$ROTOR" "$W/merge_empty"
step "$LOGS" mergeMeshes_add_case_without_mesh mergeMeshes -overwrite "$W/merge_master" "$W/merge_empty"

# 9-10. checkMesh and foamMeshToFluent on a case without a mesh.
new_case "$ROTOR" "$W/no_mesh"
step "$LOGS" checkMesh_no_mesh checkMesh -case "$W/no_mesh"
step "$LOGS" foamMeshToFluent_no_mesh foamMeshToFluent -case "$W/no_mesh"

keep_logs "$LOGS"
cat "$LOGS/exit_codes.txt"
