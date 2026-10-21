#!/usr/bin/env bash
# Start the rotating-machinery app (VAWT, HAWT, Francis, Custom) inside WSL, serving it on
# 127.0.0.1 only, and open it in the Windows browser once it answers. Stop with Ctrl+C
# (or close the window); an active run is cancelled first.
#
# Nothing has to be set (owner, M2a: no terminal for the user). The Python that has the
# app's packages is found in this order, and the one found is remembered:
#   VAWT_PYTHON; the "python" of ~/.machines/settings.json; ~/venvs/*/bin/python,
#   ~/.venv/bin/python and the app's .venv/bin/python; python3.
# Optional settings (environment variables):
#   OPENFOAM_BASHRC    OpenFOAM environment to source
#                      (default: /usr/lib/openfoam/openfoam2512/etc/bashrc)
#   MACHINES_PORT      port (default: 8502)
#   MACHINES_PROJECTS_DIR  projects folder (default: ~/machine_projects)
#   MACHINES_NO_BROWSER    set to 1 to not open the browser
#
# No proxy setting is read or written here; the app makes no outside requests.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
app_dir="$(dirname "$here")"
bashrc="${OPENFOAM_BASHRC:-/usr/lib/openfoam/openfoam2512/etc/bashrc}"
port="${MACHINES_PORT:-8502}"
settings="${MACHINES_SETTINGS:-$HOME/.machines/settings.json}"

if [ -f "$bashrc" ]; then
    set +eu  # OpenFOAM's bashrc uses unset variables and non-zero tests
    # shellcheck disable=SC1090
    source "$bashrc"
    set -eu
else
    echo "OpenFOAM environment not found at $bashrc; set OPENFOAM_BASHRC." >&2
    echo "The app starts, but meshing cannot run." >&2
fi

has_streamlit() { [ -x "$1" ] || command -v "$1" >/dev/null 2>&1 || return 1; "$1" -c "import streamlit" 2>/dev/null; }
remembered() {
    [ -f "$settings" ] || return 0
    python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("python", ""))' \
        "$settings" 2>/dev/null || true
}
python=""
for candidate in "${VAWT_PYTHON:-}" "$(remembered)" "$HOME"/venvs/*/bin/python \
                 "$HOME/.venv/bin/python" "$app_dir/.venv/bin/python" python3; do
    if [ -n "$candidate" ] && has_streamlit "$candidate"; then
        python="$candidate"
        break
    fi
done
if [ -z "$python" ]; then
    echo "No Python with the app's packages was found (tried VAWT_PYTHON, $settings," >&2
    echo "~/venvs/*/bin/python, ~/.venv, $app_dir/.venv and python3)." >&2
    echo "Install the app once: python3 -m venv ~/venvs/machines && ~/venvs/machines/bin/pip install -e $app_dir" >&2
    exit 1
fi
mkdir -p "$(dirname "$settings")"
python3 - "$settings" "$python" <<'PY' 2>/dev/null || true
import json, sys
path, python = sys.argv[1], sys.argv[2]
try:
    data = json.load(open(path))
except (OSError, ValueError):
    data = {}
if data.get("python") != python:
    data["python"] = python
    json.dump(data, open(path, "w"), indent=2)
PY

url="http://localhost:$port"
echo "Rotating machines: $url (opens in the Windows browser when ready)."
echo "Python: $python. OpenFOAM: ${WM_PROJECT_VERSION:-not sourced}. Projects: ${MACHINES_PROJECTS_DIR:-$HOME/machine_projects}"
echo "Stop with Ctrl+C or close this window."
if [ "${MACHINES_NO_BROWSER:-}" != "1" ]; then
    (
        for _ in $(seq 1 120); do
            if "$python" -c "import urllib.request,sys; urllib.request.urlopen(sys.argv[1], timeout=1)" \
                    "http://127.0.0.1:$port/_stcore/health" 2>/dev/null; then
                if command -v wslview >/dev/null 2>&1; then
                    wslview "$url" >/dev/null 2>&1
                elif [ -x /mnt/c/Windows/System32/cmd.exe ]; then
                    (cd /mnt/c && /mnt/c/Windows/System32/cmd.exe /c start "" "$url" >/dev/null 2>&1)
                fi
                exit 0
            fi
            sleep 1
        done
    ) &
fi
cd "$app_dir"
exec "$python" -m streamlit run app/machines_app.py \
    --server.address 127.0.0.1 --server.port "$port" \
    --server.headless true --browser.gatherUsageStats false
