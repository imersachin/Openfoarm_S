@echo off
rem Put a "Rotating Machines" shortcut on the Windows desktop that starts the app
rem (run_machines_app.cmd beside this file). Double-click this file once.
setlocal
set "TARGET=%~dp0run_machines_app.cmd"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Rotating Machines.lnk');" ^
  "$s.TargetPath = '%TARGET%';" ^
  "$s.WorkingDirectory = '%~dp0';" ^
  "$s.Description = 'Start the rotating machinery app (OpenFOAM in WSL) and open it in the browser';" ^
  "$s.IconLocation = '%SystemRoot%\System32\shell32.dll,12';" ^
  "$s.Save()"
if errorlevel 1 (
  echo The shortcut could not be made.
) else (
  echo "Rotating Machines" is on your desktop.
)
pause
