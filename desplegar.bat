@echo off
rem Instala o actualiza el banco en la BeagleBoard con un solo comando.
rem   desplegar.bat                    actualiza la aplicacion
rem   desplegar.bat -ConfigurarClave   una vez: entrar por SSH sin contrasena
rem   desplegar.bat -Completo          placa nueva: todo desde cero (horas)
rem   desplegar.bat -ActualizarConfig  ademas usa chips.yaml/bench.yaml nuevos
rem   desplegar.bat -Placa 192.168.0.27
rem Detalles: scripts\desplegar.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\desplegar.ps1" %*
if errorlevel 1 pause
