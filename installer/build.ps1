<#
.SYNOPSIS
    Arma dist\InstaladorBancoRemoto.exe: el asistente que instala el Banco remoto en
    esta PC o en una placa por SSH, con la aplicación adentro.

.DESCRIPTION
    1. Empaqueta la aplicación con git archive (lo que está commiteado + cambios sin commit;
       los archivos nuevos tienen que estar agregados con git add).
    2. Dibuja el ícono.
    3. PyInstaller: un solo .exe, sin consola.

    Uso:  powershell -ExecutionPolicy Bypass -File installer\build.ps1
#>
$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
Set-Location $Repo
$Py = Join-Path $Repo ".venv\Scripts\python.exe"
if (-not (Test-Path $Py)) { throw "Falta el entorno .venv (scripts\dev_run.ps1 lo crea)." }

& $Py -m pip install -q -r installer\requirements.txt
if ($LASTEXITCODE -ne 0) { throw "No se pudieron instalar paramiko y pyinstaller." }

$Build = Join-Path $Repo "installer\build"
New-Item -ItemType Directory -Force $Build | Out-Null
& $Py -m installer.paquete (Join-Path $Build "banco-remoto.tgz")
& $Py -m installer.icono (Join-Path $Build "banco-remoto.ico")
# Python embebido, bibliotecas, avrdude, dfu-util y OpenOCD/GDB de ST: la PC no necesita nada más.
& $Py -m installer.preparar
if ($LASTEXITCODE -ne 0) { throw "No se pudo preparar lo que va adentro del instalador." }

& $Py -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name InstaladorBancoRemoto `
    --icon (Join-Path $Build "banco-remoto.ico") `
    --add-data "$(Join-Path $Build 'banco-remoto.tgz');." `
    --add-data "$(Join-Path $Build 'runtime');runtime" `
    --paths $Repo `
    --distpath (Join-Path $Repo "dist") `
    --workpath (Join-Path $Build "pyi") `
    --specpath $Build `
    installer\lanzador.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller falló." }

$Exe = Join-Path $Repo "dist\InstaladorBancoRemoto.exe"
Write-Host ("Listo: {0} ({1:N1} MB)" -f $Exe, ((Get-Item $Exe).Length / 1MB)) -ForegroundColor Green
