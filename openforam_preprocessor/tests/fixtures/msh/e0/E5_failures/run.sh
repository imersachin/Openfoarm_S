#!/bin/bash
# E5 - Inputs that must fail clearly (plan E0.7), and the cost of reading the
# bounding box without converting (plan E0.6, owner answer 4).
#   Run after E1 and E2 (it reuses their .msh files from the work directory).
#   truncated:   the E2 Fluent file cut to half its size
#   h5:          a file starting with the HDF5 signature (as a .cas.h5 / .msh.h5)
#   empty:       an empty file
#   wrong_tool:  a Gmsh file to fluent3DMeshToFoam, a Fluent file to gmshToFoam
#   missing:     a file name that does not exist (fluent3DMeshToFoam)
#   size:        a 1,000,000-cell blockMesh exported to Fluent: raw box read vs
#                fluent3DMeshToFoam (times on this machine; files not kept)
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../common/lib.sh"
W=$WORK_ROOT/E5
LOGS=$HERE/logs
rm -rf "$LOGS" "$W"; mkdir -p "$LOGS" "$W/in"
FLUENT=$WORK_ROOT/E2/src/fluentInterface/src.msh
GMSH=$WORK_ROOT/E1/msh/box_22.msh
[ -f "$FLUENT" ] && [ -f "$GMSH" ] || { echo "run E1 and E2 first"; exit 2; }

head -c $(( $(stat -c %s "$FLUENT") / 2 )) "$FLUENT" > "$W/in/truncated.msh"
printf '\211HDF\r\n\032\n' > "$W/in/fake.msh.h5"; head -c 4096 /dev/zero >> "$W/in/fake.msh.h5"
: > "$W/in/empty.msh"

convert() {  # <name> <tool> <file>
    new_case "$W/$1"
    step "$LOGS" "$1" "$2" "$3" -case "$W/$1"
    if [ -f "$W/$1/constant/polyMesh/boundary" ]; then
        step "$LOGS" "$1_checkMesh" checkMesh -case "$W/$1" -allTopology -meshQuality
    fi
    keep_mesh_summary "$LOGS" "$1" "$W/$1"
}
convert truncated_fluent3DMeshToFoam fluent3DMeshToFoam "$W/in/truncated.msh"
convert truncated_fluentMeshToFoam fluentMeshToFoam "$W/in/truncated.msh"
convert h5_fluent3DMeshToFoam fluent3DMeshToFoam "$W/in/fake.msh.h5"
convert empty_fluent3DMeshToFoam fluent3DMeshToFoam "$W/in/empty.msh"
convert empty_gmshToFoam gmshToFoam "$W/in/empty.msh"
convert wrong_tool_gmsh_to_fluent3D fluent3DMeshToFoam "$GMSH"
convert wrong_tool_fluent_to_gmsh gmshToFoam "$FLUENT"
convert missing_fluent3DMeshToFoam fluent3DMeshToFoam "$W/in/missing.msh"

# Size: 100 x 100 x 100 cells.
new_case "$W/big" "$E0_DIR/E2_fluent/src"
sed -i 's/(20 10 10)/(100 100 100)/' "$W/big/system/blockMeshDict"
step "$LOGS" size_01_blockMesh blockMesh -case "$W/big" &&
step "$LOGS" size_02_foamMeshToFluent foamMeshToFluent -case "$W/big"
BIG=$W/big/fluentInterface/big.msh
printf 'big.msh %d bytes\n' "$(stat -c %s "$BIG")" > "$LOGS/size_input.txt"
step "$LOGS" size_03_raw_bbox py "$E0_DIR/common/raw_bbox.py" "$BIG"
new_case "$W/big_f3d"
step "$LOGS" size_04_fluent3DMeshToFoam fluent3DMeshToFoam "$BIG" -case "$W/big_f3d" &&
step "$LOGS" size_05_checkMesh checkMesh -case "$W/big_f3d"
step "$LOGS" size_06_raw_bbox_small py "$E0_DIR/common/raw_bbox.py" "$FLUENT" "$GMSH" \
    "$WORK_ROOT/E1/msh/box_41.msh" "$WORK_ROOT/E1/msh/box_22_bin.msh" "$WORK_ROOT/E4/rotating.msh"
rm -rf "$W/big" "$W/big_f3d"
keep_logs "$LOGS"
cat "$LOGS/exit_codes.txt"
