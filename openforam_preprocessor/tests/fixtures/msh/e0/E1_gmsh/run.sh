#!/bin/bash
# E1 - Gmsh files into OpenFOAM (plan E0.1, E0.2; Gmsh v1 of E0.7):
#   box_<v>[_bin][_nogroups]: the make_gmsh box written as MSH 2.2 / 4.1, ASCII or
#   binary, with or without physical groups -> gmshToFoam -> checkMesh
#   box_41*_as22: the 4.1 files re-saved as 2.2 with the gmsh package first
#   box_22_nowalls: inlet and outlet groups only, the walls without a group
#   box_v1: MSH 1 (legacy) -> gmshToFoam
#   box_missing: a file name that does not exist -> gmshToFoam
# Kept: logs, boundary files, zone names and the first 2 kB of every input file.
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../common/lib.sh"
W=$WORK_ROOT/E1
LOGS=$HERE/logs
rm -rf "$LOGS" "$W"; mkdir -p "$LOGS" "$W/msh"
MAKE=$E0_DIR/common/make_gmsh.py

step "$LOGS" make_box_22 py "$MAKE" box "$W/msh/box_22.msh" --version 2.2
step "$LOGS" make_box_22_bin py "$MAKE" box "$W/msh/box_22_bin.msh" --version 2.2 --binary
step "$LOGS" make_box_41 py "$MAKE" box "$W/msh/box_41.msh" --version 4.1
step "$LOGS" make_box_41_bin py "$MAKE" box "$W/msh/box_41_bin.msh" --version 4.1 --binary
step "$LOGS" make_box_22_nogroups py "$MAKE" box "$W/msh/box_22_nogroups.msh" --version 2.2 --no-groups
step "$LOGS" make_box_41_nogroups py "$MAKE" box "$W/msh/box_41_nogroups.msh" --version 4.1 --no-groups
step "$LOGS" make_box_22_nowalls py "$MAKE" box "$W/msh/box_22_nowalls.msh" --version 2.2 --no-walls
step "$LOGS" make_box_41_as22 py "$MAKE" resave "$W/msh/box_41.msh" "$W/msh/box_41_as22.msh" --version 2.2
step "$LOGS" make_box_41_bin_as22 py "$MAKE" resave "$W/msh/box_41_bin.msh" "$W/msh/box_41_bin_as22.msh" --version 2.2
step "$LOGS" make_box_41_nogroups_as22 py "$MAKE" resave "$W/msh/box_41_nogroups.msh" "$W/msh/box_41_nogroups_as22.msh" --version 2.2
step "$LOGS" make_box_v1 py "$MAKE" resave "$W/msh/box_22.msh" "$W/msh/box_v1.msh" --version 1

mkdir -p "$LOGS/heads"
for f in "$W"/msh/*.msh; do
    n=$(basename "$f" .msh)
    head -c 2048 "$f" > "$LOGS/heads/$n.head"
    printf '%-24s %10d bytes\n' "$n" "$(stat -c %s "$f")" >> "$LOGS/input_sizes.txt"
done

for n in box_22 box_22_bin box_41 box_41_bin box_22_nogroups box_41_nogroups box_22_nowalls \
         box_41_as22 box_41_bin_as22 box_41_nogroups_as22 box_v1 box_missing; do
    c=$W/$n
    new_case "$c"
    step "$LOGS" "${n}_01_gmshToFoam" gmshToFoam "$W/msh/$n.msh" -case "$c"
    [ -f "$c/constant/polyMesh/boundary" ] &&
        step "$LOGS" "${n}_02_checkMesh" checkMesh -case "$c" -allTopology -meshQuality
    keep_mesh_summary "$LOGS" "$n" "$c"
done
keep_logs "$LOGS"
cat "$LOGS/exit_codes.txt"
