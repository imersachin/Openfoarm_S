#!/bin/bash
# Regenerate the M1-MSH test meshes (committed; small) on OpenFOAM v2512 with gmsh.
#   PYTHON=<python with gmsh> bash tests/fixtures/msh/m1/make_fixtures.sh
# Gmsh: make_gmsh_small.py. Fluent: foamMeshToFluent of a blockMesh box
# (inlet/outlet patch, walls wall) and of the converted rotating Gmsh mesh,
# whose one cell section is then split into its two zones (as E6).
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
source /usr/lib/openfoam/openfoam2512/etc/bashrc >/dev/null 2>&1  # not `set -u` clean
set -eu
PYTHON=${PYTHON:-python3}
W=$(mktemp -d /tmp/msh_m1_XXXX)
E0=$HERE/../e0
"$PYTHON" "$HERE/make_gmsh_small.py" "$HERE"
# MSH 1: re-saved from the 2.2 box
"$PYTHON" "$E0/common/make_gmsh.py" resave "$HERE/box_22.msh" "$HERE/box_v1.msh" --version 1

case_dir() { rm -rf "$1"; mkdir -p "$1/system" "$1/constant"; cp "$E0"/common/system/* "$1/system/"; }

# Fluent box: 4 x 2 x 2 cells.
case_dir "$W/box"
cp "$E0/E2_fluent/src/system/blockMeshDict" "$W/box/system/"
sed -i 's/(20 10 10)/(4 2 2)/' "$W/box/system/blockMeshDict"
blockMesh -case "$W/box" > "$W/box.log"
foamMeshToFluent -case "$W/box" >> "$W/box.log"
cp "$W/box/fluentInterface/box.msh" "$HERE/fluent_box.msh"

# Fluent rotating: the Gmsh rotating mesh through OpenFOAM, cells split by zone.
case_dir "$W/rot"
gmshToFoam "$HERE/rotating_41.msh" -case "$W/rot" > "$W/rot.log"
foamMeshToFluent -case "$W/rot" >> "$W/rot.log"
n=$("$PYTHON" "$E0/common/zone_names.py" "$W/rot/constant/polyMesh/cellZones" | awk '$1=="stationary"{print $4}')
"$PYTHON" "$E0/common/split_fluent_cells.py" "$W/rot/fluentInterface/rot.msh" \
    "$HERE/fluent_rotating.msh" "$n" stationary rotating
rm -rf "$W"
ls -l "$HERE"/*.msh
