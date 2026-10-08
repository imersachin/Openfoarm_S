#!/bin/bash
# Regenerate every V0 evidence fixture (logs) on OpenFOAM v2512.
#   PYTHON=<python with the project dependencies> bash tests/fixtures/vawt/v0/run_all.sh
# Work directories go to $WORK_ROOT (default /tmp/vawt_v0); only logs and small
# mesh summaries are written into this directory.
HERE=$(cd "$(dirname "$0")" && pwd)
for e in E1_ami_two_mesh E2_single_mesh E3_absolute_layers E4_failures E6_surface_check; do
    echo "===== $e"
    bash "$HERE/$e/run.sh" 2>&1 | grep -E 'exit=' || echo "(no steps recorded)"
done
