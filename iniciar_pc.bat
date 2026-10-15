@echo off
rem Banco remoto en MODO PC: la app en esta computadora, con sus propios USB.
rem   iniciar_pc.bat               solo esta PC (http://127.0.0.1:8080)
rem   iniciar_pc.bat --tailscale   tambien desde otras PC de tu red de Tailscale
rem   iniciar_pc.bat --password    cambiar usuario o contrasena
chcp 65001 >nul
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Preparando el entorno de Python ^(solo la primera vez^)...
    where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
    if errorlevel 1 (
        echo No se pudo crear el entorno: instala Python 3.9 o mas nuevo desde https://www.python.org
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip >nul
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt "esptool>=4.7,<5"
    if errorlevel 1 (
        echo Fallo la instalacion de dependencias.
        pause
        exit /b 1
    )
)

set PYTHONIOENCODING=utf-8
".venv\Scripts\python.exe" scripts\pc_run.py %*
if errorlevel 1 pause
