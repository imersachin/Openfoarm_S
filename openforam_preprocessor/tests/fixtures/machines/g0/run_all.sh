#!/bin/bash
# Regenerate every G0 evidence fixture (logs) on OpenFOAM v2512.
#   PYTHON=<python with the project dependencies> bash tests/fixtures/machines/g0/run_all.sh
# Work directories go to $WORK_ROOT (default /tmp/machines_g0); only logs and
# small mesh summaries are written into this directory.
HERE=$(cd "$(dirname "$0")" && pwd)
for e in R1_cylinder_domain R2_imported_domain R3_hawt R4_francis R5_pole; do
    echo "===== $e"
    bash "$HERE/$e/run.sh" 2>&1 | grep -E 'exit=' || echo "(no steps recorded)"
done
