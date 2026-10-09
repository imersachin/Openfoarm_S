#!/bin/bash
# R4 - Francis-like passage from separate fluid-domain STLs (owner decision 2):
#   casing, guide (vanes), runner (rotating), draft: each meshed on its own
#   (a) merged_ami:       mergeMeshes x3 -> createPatch (stationary joint + 2 sliding AMI pairs)
#   (b) merged_stitch:    mergeMeshes x3 -> stitchMesh (stationary joint) -> createPatch (2 pairs)
#   (c) merged_ami_nc, merged_stitch_nc: as (a) and (b) with guide_nc, whose
#       discs do not match its neighbours'
#   solve: a few pimpleFoam steps (k-omega SST) on each
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../common/lib.sh"
W=$WORK_ROOT/R4
LOGS=$HERE/logs
rm -rf "$LOGS" "$W"; mkdir -p "$LOGS" "$W"

for p in casing guide guide_nc runner draft; do
    new_case "$HERE/$p" "$W/$p"
    geometry francis "$W/$p"
    step "$LOGS" "${p}_01_blockMesh" blockMesh -case "$W/$p" &&
    step "$LOGS" "${p}_02_surfaceFeatureExtract" surfaceFeatureExtract -case "$W/$p" &&
    step "$LOGS" "${p}_03_snappyHexMesh" snappyHexMesh -case "$W/$p" -overwrite || continue
    if [ "$p" = runner ]; then step "$LOGS" runner_04_topoSet topoSet -case "$W/runner"; fi
    step "$LOGS" "${p}_05_checkMesh" checkMesh -case "$W/$p" -allTopology -meshQuality
    keep_mesh_summary "$LOGS" "$p" "$W/$p"
done

merge_parts() {  # <merged-case> <label> <guide part>
    local m=$1 label=$2 guide=$3 p
    rm -rf "$m"; cp -r "$W/casing" "$m"
    for p in "$guide" runner draft; do
        step "$LOGS" "${label}_01_merge_$p" in_case "$m" mergeMeshes -overwrite "$m" "$W/$p" || return 1
    done
}

for v in ami ami_nc; do
    m=$W/merged_$v
    guide=guide; [ $v = ami_nc ] && guide=guide_nc
    merge_parts "$m" "merged_$v" $guide && cp "$HERE/merged_ami/system/"* "$m/system/" &&
    step "$LOGS" "merged_${v}_02_createPatch" createPatch -case "$m" -overwrite &&
    step "$LOGS" "merged_${v}_03_checkMesh" checkMesh -case "$m" -allTopology -meshQuality
    keep_mesh_summary "$LOGS" "merged_$v" "$m"
done

for v in stitch stitch_nc; do
    m=$W/merged_$v
    guide=guide; [ $v = stitch_nc ] && guide=guide_nc
    merge_parts "$m" "merged_$v" $guide && cp "$HERE/merged_stitch/system/"* "$m/system/" &&
    step "$LOGS" "merged_${v}_02_stitchMesh" in_case "$m" stitchMesh -overwrite -integral casing_out guide_in &&
    step "$LOGS" "merged_${v}_03_checkMesh" checkMesh -case "$m" -allTopology -meshQuality &&
    step "$LOGS" "merged_${v}_04_createPatch" createPatch -case "$m" -overwrite
    # stitchMesh writes 0/meshPhi; createPatch does not add the new patches to
    # it, so the next checkMesh fails reading it (kept as evidence), then 0/ is
    # removed (it holds no field this workflow needs).
    ls "$m/0" > "$LOGS/merged_${v}_0_after_stitch.txt" 2>/dev/null
    step "$LOGS" "merged_${v}_05_checkMesh_with_meshPhi" checkMesh -case "$m" -allTopology -meshQuality
    rm -rf "$m/0"
    step "$LOGS" "merged_${v}_06_checkMesh" checkMesh -case "$m" -allTopology -meshQuality
    keep_mesh_summary "$LOGS" "merged_$v" "$m"
done

solve "$LOGS" solve_ami "$W/merged_ami" "$HERE/solve_ami" "$HERE/solve_ami/0.sst" sst "$W/solve_ami"
solve "$LOGS" solve_ami_nc "$W/merged_ami_nc" "$HERE/solve_ami" "$HERE/solve_ami/0.sst" sst "$W/solve_ami_nc"
solve "$LOGS" solve_stitch "$W/merged_stitch" "$HERE/solve_stitch" "$HERE/solve_stitch/0.sst" sst "$W/solve_stitch"
solve "$LOGS" solve_stitch_nc "$W/merged_stitch_nc" "$HERE/solve_stitch" "$HERE/solve_stitch/0.sst" sst "$W/solve_stitch_nc"
keep_logs "$LOGS"
cat "$LOGS/exit_codes.txt"
