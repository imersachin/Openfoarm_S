#!/bin/bash
# Rotation direction from the mesh itself: angle each moved point turned about the
# axis between time 0 and the last written time of a solver smoke run.
# Run after run_all.sh: PYTHON=<venv python> bash tests/fixtures/machines/g0/common/check_rotation.sh
PYTHON=${PYTHON:-python3}
WORK_ROOT=${WORK_ROOT:-/tmp/machines_g0}
OF_BASHRC=${OF_BASHRC:-/usr/lib/openfoam/openfoam2512/etc/bashrc}
set +eu; source "$OF_BASHRC"; set -eu
check() {  # <solve case> <axis index: 0=x 2=z>
  local c=$1 ax=$2 d=$WORK_ROOT/rotdir_$(basename $1)
  rm -rf $d; cp -r $c $d
  sed -i 's/^writeFormat .*/writeFormat     ascii;/' $d/system/controlDict
  local t=$(ls $d | grep -E '^[0-9.e-]+$' | grep -v '^0$' | sort -g | tail -1)
  foamFormatConvert -case $d -time $t >/dev/null 2>&1
  "$PYTHON" - "$d/constant/polyMesh/points" "$d/$t/polyMesh/points" $ax <<'PY'
import sys, re, numpy as np
def read(p):
    t = open(p).read(); body = t[t.index('(', t.index('\n', t.index('FoamFile'))) :]
    nums = re.findall(r'\(([-\d.e+]+) ([-\d.e+]+) ([-\d.e+]+)\)', t)
    return np.array(nums, dtype=float)
a, b, ax = read(sys.argv[1]), read(sys.argv[2]), int(sys.argv[3])
i, j = [(1, 2), None, (0, 1)][ax]
moved = np.linalg.norm(a - b, axis=1) > 1e-9
r = np.hypot(a[:, i], a[:, j])
sel = moved & (r > 0.05)
d = np.angle(np.exp(1j * (np.arctan2(b[sel, j], b[sel, i]) - np.arctan2(a[sel, j], a[sel, i]))))
print(f"{sys.argv[2].split('/')[-4].removeprefix('rotdir_')}: points moved {sel.sum()} of {len(a)}; rotation about axis {'xyz'[ax]}: "
      f"mean {np.degrees(d.mean()):.4f} deg, min {np.degrees(d.min()):.4f}, max {np.degrees(d.max()):.4f}")
PY
}
check $WORK_ROOT/R3/solve_snappy_sst 0
check $WORK_ROOT/R5/solve_hole 2
check $WORK_ROOT/R4/solve_ami 2
