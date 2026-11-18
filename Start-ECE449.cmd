@echo off
setlocal
cd /d "%~dp0"
set "UV=uv.exe"
where uv.exe >nul 2>&1
if not errorlevel 1 goto run
set "UV=%CD%\.bootstrap\uv.exe"
if exist "%UV%" goto run
if not exist ".bootstrap" mkdir ".bootstrap"
echo Preparing the local Python environment...
curl.exe -fLsS --retry 2 "https://releases.astral.sh/github/uv/releases/download/0.13.7/uv-x86_64-pc-windows-msvc.zip" -o ".bootstrap\uv.zip"
if errorlevel 1 goto failed
powershell.exe -NoProfile -Command "Expand-Archive -LiteralPath '.bootstrap/uv.zip' -DestinationPath '.bootstrap' -Force"
if errorlevel 1 goto failed
if not exist "%UV%" goto failed
:run
"%UV%" run --no-project --python 3.12 --with-requirements requirements.txt python -X utf8 scripts/classroom_harness.py --open
if not errorlevel 1 exit /b 0
:failed
echo Could not start. Check the error above, the network connection, or whether port 8895 is already in use.
pause
exit /b 1
