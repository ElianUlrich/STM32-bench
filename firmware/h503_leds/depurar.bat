@echo off
rem Conecta arm-none-eabi-gdb al servidor GDB del banco (Windows).
rem Uso:  depurar.bat                         -> build\03_contador_binario.elf
rem       depurar.bat 01_blink_todos.elf
rem Antes: en la web, Servidor GDB -> Iniciar (tiene que decir "Escuchando").
rem No abras el puerto 3333 en el navegador: ocupa la única conexión GDB.
rem Variables opcionales: GDB (ruta a arm-none-eabi-gdb.exe), BANCO (IP:puerto).
if "%GDB%"=="" set GDB=C:\ST\STM32CubeIDE_1.19.0\STM32CubeIDE\plugins\com.st.stm32cube.ide.mcu.externaltools.gnu-tools-for-stm32.13.3.rel1.win32_1.0.0.202411081344\tools\bin\arm-none-eabi-gdb.exe
if "%BANCO%"=="" set BANCO=100.99.235.81:3333
set ELF=%~1
if "%ELF%"=="" set ELF=03_contador_binario.elf
"%GDB%" "%~dp0build\%ELF%" -ex "set remotetimeout 10" -ex "target extended-remote %BANCO%"
