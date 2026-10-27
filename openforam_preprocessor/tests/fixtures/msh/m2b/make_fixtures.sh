#!/bin/bash
# Regenerate the M2b test parts (committed; small) on OpenFOAM v2512:
#   bash tests/fixtures/msh/m2b/make_fixtures.sh
# blockMesh (make_parts.py) then foamMeshToFluent, as the M1 Fluent box.
HERE=$(cd "$(dirname "$0")" && pwd)
# shellcheck disable=SC1091
source /usr/lib/openfoam/openfoam2512/etc/bashrc >/dev/null 2>&1  # not `set -u` clean
set -eu
PYTHON=${PYTHON:-python3}
W=$(mktemp -d /tmp/msh_m2b_XXXX)
E0=$HERE/../e0
"$PYTHON" "$HERE/make_parts.py" "$W"
for part in casing vanes runner outer; do
    mkdir -p "$W/$part/constant"
    cp "$E0"/common/system/controlDict "$E0"/common/system/fv* "$W/$part/system/"
    blockMesh -case "$W/$part" > "$W/$part.log"
    foamMeshToFluent -case "$W/$part" >> "$W/$part.log"
    cp "$W/$part/fluentInterface/$part.msh" "$HERE/$part.msh"
done
rm -rf "$W"
ls -l "$HERE"/*.msh
