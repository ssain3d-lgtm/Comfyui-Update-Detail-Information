@echo off
chcp 65001 >nul 2>&1
setlocal
title ComfyUI update report
cd /d "%~dp0"

set "RC=0"
set "NOPAUSE="
set "SCRIPT=%~dp0_update-report\update_report.py"
set "ARGS=%*"

if /i "%~1"=="--no-pause" set "NOPAUSE=1"
if /i "%~1"=="--reinject" set "SCRIPT=%~dp0_update-report\inject_hook.py"
if /i "%~1"=="--reinject" set "ARGS="

:: ---- find a python that can see ComfyUI's site-packages ----
set "PY="
if exist "%~dp0python_embeded\python.exe" set "PY=%~dp0python_embeded\python.exe"
if not defined PY if exist "%~dp0..\python_embeded\python.exe" set "PY=%~dp0..\python_embeded\python.exe"
if not defined PY if exist "%~dp0venv\Scripts\python.exe" set "PY=%~dp0venv\Scripts\python.exe"
if not defined PY if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY if exist "%~dp0ComfyUI\venv\Scripts\python.exe" set "PY=%~dp0ComfyUI\venv\Scripts\python.exe"
if not defined PY if exist "%~dp0ComfyUI\.venv\Scripts\python.exe" set "PY=%~dp0ComfyUI\.venv\Scripts\python.exe"
if not defined PY goto :TRY_PATH
goto :RUN

:TRY_PATH
where python >nul 2>&1
if errorlevel 1 goto :NO_PYTHON
set "PY=python"

:RUN
if not exist "%SCRIPT%" goto :NO_SCRIPT
"%PY%" "%SCRIPT%" %ARGS%
set "RC=%ERRORLEVEL%"
if defined NOPAUSE goto :DONE
pause

:DONE
endlocal & exit /b %RC%

:NO_PYTHON
echo [ERROR] No python found.
echo         Looked for python_embeded, venv, .venv and python on PATH.
echo         Portable ComfyUI users - put this file next to python_embeded.
pause
endlocal & exit /b 1

:NO_SCRIPT
echo [ERROR] Missing "_update-report" folder next to this file.
echo         Copy the whole folder from the repository, not just this .bat.
pause
endlocal & exit /b 1
