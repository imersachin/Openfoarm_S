# Shared helpers for the G0 experiment scripts (sourced, not executed).
#
# Environment: OpenFOAM v2512 (openfoam.com) at /usr/lib/openfoam/openfoam2512.
# Each experiment copies its dictionaries to a scratch work directory, runs the
# commands there, and copies the logs back into <experiment>/logs/.
# Same conventions as tests/fixtures/vawt/v0/common/lib.sh.

OF_BASHRC=${OF_BASHRC:-/usr/lib/openfoam/openfoam2512/etc/bashrc}
# shellcheck disable=SC1090
source "$OF_BASHRC" >/dev/null 2>&1  # the OpenFOAM bashrc is not `set -u` clean
command -v blockMesh >/dev/null || { echo "OpenFOAM not available from $OF_BASHRC"; exit 2; }
set -u

G0_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
REPO_ROOT=$(cd "$G0_DIR/../../../.." && pwd)
PYTHON=${PYTHON:-python3}
export PYTHONDONTWRITEBYTECODE=1  # no __pycache__ in the repository
# Not under a directory whose name ends in "processor" (V0 finding R9).
WORK_ROOT=${WORK_ROOT:-/tmp/machines_g0}

# new_case <src-dir-with-system/> <dest> : copy dictionaries + common files
new_case() {
    local src=$1 dest=$2
    rm -rf "$dest"
    mkdir -p "$dest/system" "$dest/constant/triSurface"
    cp "$G0_DIR"/common/system/* "$dest/system/"
    cp "$src"/system/* "$dest/system/"
}

# geometry <set> <case> : write one geometry set into constant/triSurface
geometry() {
    (cd "$REPO_ROOT" && "$PYTHON" -m tests.fixtures.machines.g0.common.make_geometry \
        "$1" "$2/constant/triSurface" >/dev/null)
}

# step <logdir> <name> <command...> : run, keep stdout/stderr, record the exit code
step() {
    local logdir=$1 name=$2; shift 2
    mkdir -p "$logdir"
    local start=$SECONDS
    "$@" >"$logdir/$name.log" 2>"$logdir/$name.stderr.log"
    local rc=$?
    printf '%-40s exit=%d  %4ds  %s\n' "$name" "$rc" $((SECONDS - start)) "$*" \
        | sed "s#$WORK_ROOT#<work>#g" >>"$logdir/exit_codes.txt"
    [ -s "$logdir/$name.stderr.log" ] || rm -f "$logdir/$name.stderr.log"
    return $rc
}

# in_case <case> <command...> : run a command from the case directory (R9)
in_case() {
    local dir=$1; shift
    (cd "$dir" && "$@")
}

# keep_mesh_summary <logdir> <prefix> <case> : boundary and zone heads
keep_mesh_summary() {
    local logdir=$1 prefix=$2 case=$3
    [ -f "$case/constant/polyMesh/boundary" ] && cp "$case/constant/polyMesh/boundary" "$logdir/${prefix}_boundary"
    [ -f "$case/constant/polyMesh/cellZones" ] && head -c 1500 "$case/constant/polyMesh/cellZones" > "$logdir/${prefix}_cellZones.head"
    return 0
}

# solve <logdir> <name> <mesh-case> <solve-src> <zero-dir> <turbulence> <work-dest>
#   Copy a finished mesh, add the solver files (common/solver + <solve-src>),
#   and run a few pimpleFoam time steps. Evidence only: never part of the app.
solve() {
    local logdir=$1 name=$2 mesh=$3 src=$4 zero=$5 turb=$6 dest=$7
    rm -rf "$dest"; mkdir -p "$dest"
    cp -r "$mesh/constant" "$mesh/system" "$dest/"
    rm -rf "$dest/constant/triSurface" "$dest/constant/extendedFeatureEdgeMesh"
    cp "$G0_DIR"/common/solver/fvSchemes "$G0_DIR"/common/solver/fvSolution "$dest/system/"
    cp "$src"/system/controlDict "$dest/system/"
    cp "$src"/constant/* "$dest/constant/"
    cp "$G0_DIR/common/solver/turbulenceProperties.$turb" "$dest/constant/turbulenceProperties"
    cp -r "$zero" "$dest/0"
    step "$logdir" "$name" in_case "$dest" pimpleFoam
    local rc=$?
    # Small evidence: AMI weights per time step.
    find "$dest/postProcessing" -name '*.dat' 2>/dev/null | while read -r f; do
        cp "$f" "$logdir/${name}_$(basename "$(dirname "$(dirname "$f")")")_$(basename "$f")"
    done
    return $rc
}

# keep_logs <logdir> : make logs path-independent (work dir -> <work>)
keep_logs() {
    sed -i "s#$WORK_ROOT#<work>#g" "$1"/*.log "$1"/exit_codes.txt "$1"/*_boundary 2>/dev/null || true
}
