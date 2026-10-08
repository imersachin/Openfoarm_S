#!/bin/bash
# E3 - absolute layer sizing (spec section 10 Q5), on the E1 rotor sub-case.
# Variants differ only in addLayersControls, edited with foamDictionary; the
# effective addLayersControls of each variant is saved next to its logs.
#   E3a relativeSizes false + thicknessModel firstAndExpansion
#   E3b relativeSizes false + individual entries (no thicknessModel)
#   E3c relativeSizes false + thicknessModel firstAndExpansion, firstLayerThickness missing
# First layer = D/5000 = 2.08e-4 m (V1 preset); 3 layers, ratio 1.2;
# minThickness 1e-4 m (total of 3 layers is 7.57e-4 m).
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../common/lib.sh"
W=$WORK_ROOT/E3
LOGS=$HERE/logs
rm -rf "$LOGS" "$W"; mkdir -p "$LOGS" "$W"

base() {  # base <case>
    new_case "$HERE/../E1_ami_two_mesh/rotor" "$1"
    geometry "$1"
    foamDictionary "$1/system/snappyHexMeshDict" -entry addLayersControls/relativeSizes -set false >/dev/null
    foamDictionary "$1/system/snappyHexMeshDict" -entry addLayersControls/minThickness -set 1e-4 >/dev/null
    foamDictionary "$1/system/snappyHexMeshDict" -entry addLayersControls/finalLayerThickness -remove >/dev/null
}

variant() {  # variant <name> ; runs the case and records the result
    local c=$W/$1
    foamDictionary "$c/system/snappyHexMeshDict" -entry addLayersControls -expand \
        > "$LOGS/$1_addLayersControls" 2>&1
    step "$LOGS" "$1_01_blockMesh" blockMesh -case "$c" &&
    step "$LOGS" "$1_02_surfaceFeatureExtract" surfaceFeatureExtract -case "$c" &&
    step "$LOGS" "$1_03_snappyHexMesh" snappyHexMesh -case "$c" -overwrite
}

base "$W/E3a"
foamDictionary "$W/E3a/system/snappyHexMeshDict" -entry addLayersControls/thicknessModel -add firstAndExpansion >/dev/null
foamDictionary "$W/E3a/system/snappyHexMeshDict" -entry addLayersControls/firstLayerThickness -add 2.08e-4 >/dev/null
variant E3a

base "$W/E3b"
foamDictionary "$W/E3b/system/snappyHexMeshDict" -entry addLayersControls/firstLayerThickness -add 2.08e-4 >/dev/null
variant E3b

base "$W/E3c"
foamDictionary "$W/E3c/system/snappyHexMeshDict" -entry addLayersControls/thicknessModel -add firstAndExpansion >/dev/null
variant E3c

# E3d: as E3a, but the first layer chosen so the final (3rd) layer is ~0.3 of the
# finest blade cell (D/22/4 = 0.0118 m): 0.3 * 0.0118 / 1.2^2 = 2.46e-3 m.
# E3a/E3b (D/5000) extrude 0 % of faces: see the notes.
base "$W/E3d"
foamDictionary "$W/E3d/system/snappyHexMeshDict" -entry addLayersControls/thicknessModel -add firstAndExpansion >/dev/null
foamDictionary "$W/E3d/system/snappyHexMeshDict" -entry addLayersControls/firstLayerThickness -add 2.46e-3 >/dev/null
variant E3d

keep_logs "$LOGS"
cat "$LOGS/exit_codes.txt"
