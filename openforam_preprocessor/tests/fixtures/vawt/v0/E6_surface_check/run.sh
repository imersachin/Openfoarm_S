#!/bin/bash
# E6 - does surfaceCheck add information the Python validator lacks? (spec section 10 Q8)
# Runs surfaceCheck on the closed rotor, an open copy and a copy with one body
# inside-out, and records what the Python checks report for the same files.
HERE=$(cd "$(dirname "$0")" && pwd)
source "$HERE/../common/lib.sh"
W=$WORK_ROOT/E6
LOGS=$HERE/logs
rm -rf "$LOGS" "$W"; mkdir -p "$LOGS" "$W"
(cd "$REPO_ROOT" && "$PYTHON" -m tests.fixtures.vawt.v0.common.make_geometry "$W" >/dev/null)

for s in rotor rotor_open rotor_inverted; do
    (cd "$W" && step "$LOGS" "surfaceCheck_$s" surfaceCheck "$s.stl")
done
(cd "$REPO_ROOT" && "$PYTHON" -m tests.fixtures.vawt.v0.E6_surface_check.compare_python \
    "$W" "$LOGS/python_checks.json" >/dev/null)
keep_logs "$LOGS"
cat "$LOGS/exit_codes.txt"
