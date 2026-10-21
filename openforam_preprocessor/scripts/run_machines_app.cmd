@echo off
rem Start the rotating-machinery app inside WSL (where OpenFOAM is installed), serve it on
rem 127.0.0.1 and open http://localhost:8502 in the browser once it answers. Double-click
rem this file (or the desktop shortcut that make_desktop_shortcut.cmd makes); nothing has
rem to be set: run_machines_app.sh finds the Python that has the app's packages.
rem
rem Optional, set before running this file:
rem   VAWT_WSL_DISTRO    WSL distribution (default: your default distribution)
rem   VAWT_PYTHON, MACHINES_PORT, MACHINES_PROJECTS_DIR, OPENFOAM_BASHRC,
rem   MACHINES_NO_BROWSER: passed to scripts/run_machines_app.sh (see that file).
setlocal
set "WSLCMD=wsl"
if defined VAWT_WSL_DISTRO set "WSLCMD=wsl -d %VAWT_WSL_DISTRO%"
set "PASS=VAWT_PYTHON/u:MACHINES_PORT/u:MACHINES_PROJECTS_DIR/u:OPENFOAM_BASHRC/u:MACHINES_NO_BROWSER/u"
if defined WSLENV (set "WSLENV=%WSLENV%:%PASS%") else (set "WSLENV=%PASS%")
for /f "usebackq delims=" %%P in (`%WSLCMD% wslpath -a "%~dp0run_machines_app.sh"`) do set "SCRIPT=%%P"
%WSLCMD% -- bash "%SCRIPT%"
if errorlevel 1 pause
