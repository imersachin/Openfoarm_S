# Shared helpers for the V0 experiment scripts (sourced, not executed).
#
# Environment: OpenFOAM v2512 (openfoam.com) at /usr/lib/openfoam/openfoam2512.
# Each experiment copies its dictionaries to a scratch work directory, runs the
# commands there, and copies the logs back into <experiment>/logs/.

OF_BASHRC=${OF_BASHRC:-/usr/lib/openfoam/openfoam2512/etc/bashrc}
# shellcheck disable=SC1090
source "$OF_BASHRC" >/dev/null 2>&1  # the OpenFOAM bashrc is not `set -u` clean
command -v blockMesh >/dev/null || { echo "OpenFOAM not available from $OF_BASHRC"; exit 2; }
set -u

V0_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
REPO_ROOT=$(cd "$V0_DIR/../../../.." && pwd)
PYTHON=${PYTHON:-python3}
export PYTHONDONTWRITEBYTECODE=1  # no __pycache__ in the repository
WORK_ROOT=${WORK_ROOT:-/tmp/vawt_v0}

# new_case <src-dir-with-system/> <dest> : copy dictionaries + common files
new_case() {
    local src=$1 dest=$2
    rm -rf "$dest"
    mkdir -p "$dest/system" "$dest/constant/triSurface"
    cp "$V0_DIR"/common/system/* "$dest/system/"
    cp "$src"/system/* "$dest/system/"
}

# geometry <case> : write the experiment surfaces into constant/triSurface
geometry() {
    (cd "$REPO_ROOT" && "$PYTHON" -m tests.fixtures.vawt.v0.common.make_geometry \
        "$1/constant/triSurface" >/dev/null)
}

# step <logdir> <name> <command...> : run, keep stdout/stderr, record the exit code
step() {
    local logdir=$1 name=$2; shift 2
    mkdir -p "$logdir"
    "$@" >"$logdir/$name.log" 2>"$logdir/$name.stderr.log"
    local rc=$?
    printf '%-40s exit=%d  %s\n' "$name" "$rc" "$*" | sed "s#$WORK_ROOT#<work>#g" \
        >>"$logdir/exit_codes.txt"
    [ -s "$logdir/$name.stderr.log" ] || rm -f "$logdir/$name.stderr.log"
    return $rc
}

# keep_logs <logdir> : make logs path-independent (work dir -> <work>)
keep_logs() {
    sed -i "s#$WORK_ROOT#<work>#g" "$1"/*.log "$1"/exit_codes.txt 2>/dev/null || true
}
