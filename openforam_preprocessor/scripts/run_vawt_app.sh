#!/usr/bin/env bash
# Start the VAWT mesh generator inside WSL, serving it on 127.0.0.1 only.
# Open the printed address in the Windows browser: WSL forwards Windows'
# localhost to it. Stop with Ctrl+C; an active run is cancelled first.
#
# Optional settings (environment variables):
#   OPENFOAM_BASHRC    OpenFOAM environment to source
#                      (default: /usr/lib/openfoam/openfoam2512/etc/bashrc)
#   VAWT_PYTHON        Python that has the app's dependencies (default: python3)
#   VAWT_PORT          port (default: 8501)
#   VAWT_PROJECTS_DIR  projects folder (default: ~/vawt_projects)
#
# No proxy setting is read or written here; the app makes no outside requests.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
app_dir="$(dirname "$here")"
bashrc="${OPENFOAM_BASHRC:-/usr/lib/openfoam/openfoam2512/etc/bashrc}"
python="${VAWT_PYTHON:-python3}"
port="${VAWT_PORT:-8501}"

if [ -f "$bashrc" ]; then
    set +eu  # OpenFOAM's bashrc uses unset variables and non-zero tests
    # shellcheck disable=SC1090
    source "$bashrc"
    set -eu
else
    echo "OpenFOAM environment not found at $bashrc; set OPENFOAM_BASHRC." >&2
    echo "The app starts, but meshing cannot run." >&2
fi

if ! "$python" -c "import streamlit" 2>/dev/null; then
    echo "$python cannot import streamlit; set VAWT_PYTHON to the Python of the" >&2
    echo "environment where the app is installed (pip install -e .)." >&2
    exit 1
fi

echo "VAWT mesh generator: open http://localhost:$port in the Windows browser."
echo "OpenFOAM: ${WM_PROJECT_VERSION:-not sourced}. Projects: ${VAWT_PROJECTS_DIR:-$HOME/vawt_projects}"
echo "Stop with Ctrl+C."
cd "$app_dir"
exec "$python" -m streamlit run app/vawt_app.py \
    --server.address 127.0.0.1 --server.port "$port" \
    --server.headless true --browser.gatherUsageStats false
