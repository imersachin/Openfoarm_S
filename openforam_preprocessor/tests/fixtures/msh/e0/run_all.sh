#!/bin/bash
# Regenerate every M1-MSH E0 evidence fixture (logs) on OpenFOAM v2512.
#   PYTHON=<python with the project dependencies and gmsh> bash tests/fixtures/msh/e0/run_all.sh
# Work directories go to $WORK_ROOT (default /tmp/msh_e0); only logs, boundary
# files, zone names and file heads are written into this directory.
# E5 reuses files from E1, E2 and E4, so the order matters.
HERE=$(cd "$(dirname "$0")" && pwd)
for e in E1_gmsh E2_fluent E3_baffles E4_rotating E5_failures; do
    echo "===== $e"
    bash "$HERE/$e/run.sh" 2>&1 | grep -E 'exit=' || echo "(no steps recorded)"
done
