@echo off
REM Windows launcher: double-click this file (after extracting the zip).
cd /d "%~dp0"
title FactoryGuard AI

if not exist "%~dp0backend\app\main.py" (
  echo.
  echo  Please EXTRACT the zip first: right-click the zip ^> Extract All,
  echo  then double-click run.bat inside the extracted folder.
  echo.
  pause
  exit /b 1
)

set "PYCMD="
where py >nul 2>nul && set "PYCMD=py -3"
if not defined PYCMD (
  python -c "import sys" >nul 2>nul && set "PYCMD=python"
)
if not defined PYCMD (
  echo.
  echo  Python 3.10+ is not installed.
  echo  1. Download it from https://www.python.org/downloads/
  echo  2. In the installer, tick "Add python.exe to PATH", then Install.
  echo  3. Close this window and double-click run.bat again.
  echo.
  start https://www.python.org/downloads/
  pause
  exit /b 1
)

echo Using: %PYCMD%
%PYCMD% run.py %*
echo.
echo The server has stopped. Close this window, or press a key.
pause >nul
