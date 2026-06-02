@echo off
setlocal
cd /d "%~dp0"

if exist "%~dp0runtime\python" set "PATH=%~dp0runtime\python;%PATH%"
if exist "%~dp0runtime\ffmpeg\bin" set "PATH=%~dp0runtime\ffmpeg\bin;%PATH%"
if exist "%~dp0runtime\webp\bin" set "PATH=%~dp0runtime\webp\bin;%PATH%"

set "PYTHON_EXE=pythonw"
if exist "%~dp0runtime\python\pythonw.exe" set "PYTHON_EXE=%~dp0runtime\python\pythonw.exe"

start "" "%PYTHON_EXE%" "%~dp0animation_server.py"
