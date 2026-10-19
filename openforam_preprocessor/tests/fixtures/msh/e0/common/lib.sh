# Shared helpers for the M1-MSH E0 experiment scripts (sourced, not executed).
#
# Environment: OpenFOAM v2512 (openfoam.com) at /usr/lib/openfoam/openfoam2512.
# Each experiment runs its commands in a scratch work directory and copies the
# logs back into <experiment>/logs/. Same conventions as
# tests/fixtures/machines/g0/common/lib.sh.

OF_BASHRC=${OF_BASHRC:-/usr/lib/openfoam/openfoam2512/etc/bashrc}
# shellcheck disable=SC1090
source "$OF_BASHRC" >/dev/null 2>&1  # the OpenFOAM bashrc is not `set -u` clean
command -v blockMesh >/dev/null || { echo "OpenFOAM not available from $OF_BASHRC"; exit 2; }
set -u

E0_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
REPO_ROOT=$(cd "$E0_DIR/../../../.." && pwd)
PYTHON=${PYTHON:-python3}
export PYTHONDONTWRITEBYTECODE=1  # no __pycache__ in the repository
export PYTHONPATH=$REPO_ROOT
# Not under a directory whose name ends in "processor" (V0 finding R9).
WORK_ROOT=${WORK_ROOT:-/tmp/msh_e0}

# new_case <dest> [src-dir-with-system/] : an empty case with the common controlDict
new_case() {
    local dest=$1 src=${2:-}
    rm -rf "$dest"
    mkdir -p "$dest/system" "$dest/constant"
    cp "$E0_DIR"/common/system/* "$dest/system/"
    if [ -n "$src" ]; then cp "$src"/system/* "$dest/system/"; fi
}

# step <logdir> <name> <command...> : run, keep stdout/stderr, record the exit code
step() {
    local logdir=$1 name=$2; shift 2
    mkdir -p "$logdir"
    local start=$SECONDS
    "$@" >"$logdir/$name.log" 2>"$logdir/$name.stderr.log"
    local rc=$?
    printf '%-40s exit=%d  %4ds  %s\n' "$name" "$rc" $((SECONDS - start)) "$*" \
        | sed "s#$WORK_ROOT#<work>#g; s#$REPO_ROOT#<repo>#g" >>"$logdir/exit_codes.txt"
    [ -s "$logdir/$name.stderr.log" ] || rm -f "$logdir/$name.stderr.log"
    return $rc
}

# in_case <case> <command...> : run a command from the case directory (R9)
in_case() {
    local dir=$1; shift
    (cd "$dir" && "$@")
}

# py <args...> : a helper script of this directory, from the repository root
py() {
    (cd "$REPO_ROOT" && "$PYTHON" "$@")
}

# keep_mesh_summary <logdir> <prefix> <case> : boundary, zone names, whether a mesh exists
keep_mesh_summary() {
    local logdir=$1 prefix=$2 case=$3 mesh=$3/constant/polyMesh
    if [ -f "$mesh/boundary" ]; then
        cp "$mesh/boundary" "$logdir/${prefix}_boundary"
    else
        echo "no constant/polyMesh/boundary" > "$logdir/${prefix}_boundary"
    fi
    for z in cellZones faceZones pointZones; do
        [ -f "$mesh/$z" ] && py "$E0_DIR/common/zone_names.py" "$mesh/$z" > "$logdir/${prefix}_$z.txt"
    done
    return 0
}

# keep_logs <logdir> : make logs path-independent (work dir -> <work>)
keep_logs() {
    sed -i "s#$WORK_ROOT#<work>#g; s#$REPO_ROOT#<repo>#g" "$1"/*.log "$1"/*.txt "$1"/*_boundary \
        2>/dev/null || true
}
