#!/bin/bash
# E4 - A rotating case from an imported mesh (plan E0.5):
#   gmsh: make_gmsh rotating (MSH 4.1 ASCII; two volumes, two copies of the zone
#         surface) -> gmshToFoam -> checkMesh
#   ami:  copy -> foamDictionary (rotor: wall) -> createPatch (AMI1/AMI2 from
#         ami_stat/ami_rot) -> checkMesh -> the G7 folder checks
#   project: the G6 case setup on that mesh (e4_case.py), mesh files hashed
#         before and after -> pimpleFoam (10 degrees of rotation)
#   fluent: the ami mesh -> foamMeshToFluent -> fluent3DMeshToFoam -> checkMesh
#         (does a rotating case survive the Fluent route?)
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../common/lib.sh"
W=$WORK_ROOT/E4
LOGS=$HERE/logs
rm -rf "$LOGS" "$W"; mkdir -p "$LOGS" "$W"

step "$LOGS" gmsh_00_make py "$E0_DIR/common/make_gmsh.py" rotating "$W/rotating.msh" --version 4.1
head -c 2048 "$W/rotating.msh" > "$LOGS/rotating_msh.head"
new_case "$W/gmsh"
step "$LOGS" gmsh_01_gmshToFoam gmshToFoam "$W/rotating.msh" -case "$W/gmsh" &&
step "$LOGS" gmsh_02_checkMesh checkMesh -case "$W/gmsh" -allTopology -meshQuality
keep_mesh_summary "$LOGS" gmsh "$W/gmsh"

rm -rf "$W/ami"; cp -r "$W/gmsh" "$W/ami"; cp "$HERE/system/createPatchDict" "$W/ami/system/"
step "$LOGS" ami_01_setWallType in_case "$W/ami" foamDictionary constant/polyMesh/boundary \
    -entry entry0/rotor/type -set wall &&
step "$LOGS" ami_02_createPatch createPatch -case "$W/ami" -overwrite &&
step "$LOGS" ami_03_checkMesh checkMesh -case "$W/ami" -allTopology -meshQuality
keep_mesh_summary "$LOGS" ami "$W/ami"
step "$LOGS" ami_04_folderChecks py "$HERE/e4_case.py" folder "$W/ami" "$LOGS/ami_03_checkMesh.log"

step "$LOGS" project_01_caseSetup py "$HERE/e4_case.py" setup "$W/ami" "$W/project" &&
step "$LOGS" project_02_pimpleFoam in_case "$W/project/case" pimpleFoam
step "$LOGS" project_03_meshUnchanged py "$HERE/../common/compare_points.py" \
    "$W/ami/constant/polyMesh" "$W/project/case/constant/polyMesh"
cp "$W/project/case/system/controlDict" "$LOGS/project_controlDict"
cp "$W/project/case/constant/dynamicMeshDict" "$LOGS/project_dynamicMeshDict"

step "$LOGS" fluent_01_foamMeshToFluent foamMeshToFluent -case "$W/ami"
grep -aE '^\((39|45) ' "$W/ami/fluentInterface/ami.msh" > "$LOGS/fluent_msh_zones.txt"
new_case "$W/fluent"
step "$LOGS" fluent_02_fluent3DMeshToFoam fluent3DMeshToFoam "$W/ami/fluentInterface/ami.msh" \
    -case "$W/fluent" &&
step "$LOGS" fluent_03_checkMesh checkMesh -case "$W/fluent" -allTopology -meshQuality
keep_mesh_summary "$LOGS" fluent "$W/fluent"
keep_logs "$LOGS"
cat "$LOGS/exit_codes.txt"
