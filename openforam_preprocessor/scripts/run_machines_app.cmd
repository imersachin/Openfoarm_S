@echo off
rem Start the rotating-machinery app inside WSL (where OpenFOAM is installed) and
rem serve it on 127.0.0.1; then open http://localhost:8502 in the browser.
rem
rem Optional, set before running this file:
rem   VAWT_WSL_DISTRO    WSL distribution (default: your default distribution)
rem   VAWT_PYTHON, MACHINES_PORT, MACHINES_PROJECTS_DIR, OPENFOAM_BASHRC: passed to
rem   scripts/run_machines_app.sh (Linux paths; see that file).
setlocal
set "WSLCMD=wsl"
if defined VAWT_WSL_DISTRO set "WSLCMD=wsl -d %VAWT_WSL_DISTRO%"
set "PASS=VAWT_PYTHON/u:MACHINES_PORT/u:MACHINES_PROJECTS_DIR/u:OPENFOAM_BASHRC/u"
if defined WSLENV (set "WSLENV=%WSLENV%:%PASS%") else (set "WSLENV=%PASS%")
for /f "usebackq delims=" %%P in (`%WSLCMD% wslpath -a "%~dp0run_machines_app.sh"`) do set "SCRIPT=%%P"
%WSLCMD% -- bash "%SCRIPT%"
