#!/bin/bash
# E6 - Where is the Fluent cell zone lost (owner review of E0)?
#   Run after E4 (it reuses the E4 ami mesh and its foamMeshToFluent export).
#   export:    the cell sections and zone names foamMeshToFluent wrote
#   two_zones: that file hand-edited into two cell zones (stationary cells
#              0..23407 and rotating 23408..24196, the E4 cellZones, which
#              are contiguous) -> fluent3DMeshToFoam -> checkMesh
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../common/lib.sh"
W=$WORK_ROOT/E6
LOGS=$HERE/logs
rm -rf "$LOGS" "$W"; mkdir -p "$LOGS" "$W"
EXPORT=$WORK_ROOT/E4/ami/fluentInterface/ami.msh
[ -f "$EXPORT" ] || { echo "run E4 first"; exit 2; }

cell_sections() {  # <msh> <prefix> : the cell section headers and the zone names
    grep -aE '^\((12|39) ' "$1" > "$LOGS/$2_msh_cells.txt"
}
cell_sections "$EXPORT" export
py "$E0_DIR/common/zone_names.py" "$WORK_ROOT/E4/ami/constant/polyMesh/cellZones" \
    > "$LOGS/export_source_cellZones.txt"

step "$LOGS" two_zones_00_split py "$E0_DIR/common/split_fluent_cells.py" "$EXPORT" \
    "$W/two_zones.msh" 23408 stationary rotating
cell_sections "$W/two_zones.msh" two_zones
new_case "$W/two_zones"
step "$LOGS" two_zones_01_fluent3DMeshToFoam fluent3DMeshToFoam "$W/two_zones.msh" \
    -case "$W/two_zones" &&
step "$LOGS" two_zones_02_checkMesh checkMesh -case "$W/two_zones" -allTopology -meshQuality
keep_mesh_summary "$LOGS" two_zones "$W/two_zones"
keep_logs "$LOGS"
cat "$LOGS/exit_codes.txt"
