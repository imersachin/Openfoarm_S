#!/bin/bash
# E2 - Fluent round trip (plan E0.3) and scaling (plan E0.6):
#   src: blockMesh (20 x 10 x 10) -> topoSet (cell zone "half") -> checkMesh
#        -> foamMeshToFluent (ASCII .msh)
#   f3d: fluent3DMeshToFoam on that .msh -> checkMesh
#   f2d: fluentMeshToFoam on that .msh -> checkMesh
#   f2d_zones: fluentMeshToFoam -writeZones
#   mm: the src mesh scaled to mm (transformPoints) and exported, then
#       mm_f3d_scale: fluent3DMeshToFoam -scale 0.001
#       mm_transform: fluent3DMeshToFoam (scale 1) -> transformPoints -scale 0.001
# Kept: logs, boundary files, zone names, the .msh header and zone sections.
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../common/lib.sh"
W=$WORK_ROOT/E2
LOGS=$HERE/logs
rm -rf "$LOGS" "$W"; mkdir -p "$LOGS" "$W"

# fluent_summary <msh> <prefix> : header (first 1.5 kB) and the zone sections of a .msh
fluent_summary() {
    head -c 1500 "$1" > "$LOGS/$2_msh.head"
    grep -aE '^\((39|45|12|13|10) ' "$1" | grep -avE '^\(1[023] \([0-9a-f]+ [0-9a-f]+ [0-9a-f]+ [0-9a-f]+ [0-9a-f]+\)\($' \
        | head -100 > "$LOGS/$2_msh_sections.txt"
    printf '%s %d bytes\n' "$2" "$(stat -c %s "$1")" >> "$LOGS/input_sizes.txt"
}

new_case "$W/src" "$HERE/src"
step "$LOGS" src_01_blockMesh blockMesh -case "$W/src" &&
step "$LOGS" src_02_topoSet topoSet -case "$W/src" &&
step "$LOGS" src_03_checkMesh checkMesh -case "$W/src" -allTopology -meshQuality &&
step "$LOGS" src_04_foamMeshToFluent foamMeshToFluent -case "$W/src"
keep_mesh_summary "$LOGS" src "$W/src"
MSH=$W/src/fluentInterface/src.msh
fluent_summary "$MSH" src

new_case "$W/f3d"
step "$LOGS" f3d_01_fluent3DMeshToFoam fluent3DMeshToFoam "$MSH" -case "$W/f3d" &&
step "$LOGS" f3d_02_checkMesh checkMesh -case "$W/f3d" -allTopology -meshQuality
keep_mesh_summary "$LOGS" f3d "$W/f3d"

new_case "$W/f2d"
step "$LOGS" f2d_01_fluentMeshToFoam fluentMeshToFoam "$MSH" -case "$W/f2d" &&
step "$LOGS" f2d_02_checkMesh checkMesh -case "$W/f2d" -allTopology -meshQuality
keep_mesh_summary "$LOGS" f2d "$W/f2d"

new_case "$W/f2d_zones"
step "$LOGS" f2d_zones_01_fluentMeshToFoam fluentMeshToFoam "$MSH" -writeZones -case "$W/f2d_zones" &&
step "$LOGS" f2d_zones_02_checkMesh checkMesh -case "$W/f2d_zones" -allTopology -meshQuality
keep_mesh_summary "$LOGS" f2d_zones "$W/f2d_zones"

# A mesh drawn in mm: the same box at 1000 x size.
rm -rf "$W/mm"; cp -r "$W/src" "$W/mm"; rm -rf "$W/mm/fluentInterface"
step "$LOGS" mm_01_transformPoints transformPoints -case "$W/mm" -scale "(1000 1000 1000)" &&
step "$LOGS" mm_02_foamMeshToFluent foamMeshToFluent -case "$W/mm"
MM=$W/mm/fluentInterface/mm.msh
fluent_summary "$MM" mm

new_case "$W/mm_f3d_scale"
step "$LOGS" mm_f3d_scale_01_fluent3DMeshToFoam fluent3DMeshToFoam "$MM" -scale 0.001 -case "$W/mm_f3d_scale" &&
step "$LOGS" mm_f3d_scale_02_checkMesh checkMesh -case "$W/mm_f3d_scale" -allTopology -meshQuality
keep_mesh_summary "$LOGS" mm_f3d_scale "$W/mm_f3d_scale"

new_case "$W/mm_transform"
step "$LOGS" mm_transform_01_fluent3DMeshToFoam fluent3DMeshToFoam "$MM" -case "$W/mm_transform" &&
step "$LOGS" mm_transform_02_checkMesh_scale1 checkMesh -case "$W/mm_transform" -allTopology -meshQuality &&
step "$LOGS" mm_transform_03_transformPoints transformPoints -case "$W/mm_transform" -scale "(0.001 0.001 0.001)" &&
step "$LOGS" mm_transform_04_checkMesh checkMesh -case "$W/mm_transform" -allTopology -meshQuality
keep_mesh_summary "$LOGS" mm_transform "$W/mm_transform"

# Same points after both scalings? (max abs difference, metres)
step "$LOGS" mm_compare_points py "$E0_DIR/common/compare_points.py" \
    "$W/mm_f3d_scale/constant/polyMesh" "$W/mm_transform/constant/polyMesh" "$W/src/constant/polyMesh"
keep_logs "$LOGS"
cat "$LOGS/exit_codes.txt"
