# Levanta el banco en MODO MOCK en Windows (sin hardware).
# Uso:  powershell -ExecutionPolicy Bypass -File scripts\dev_run.ps1 [-Port 8080]
# La contraseña se genera al azar en cada arranque y se muestra en la consola
# (o definí BENCH_PASSWORD_HASH para fijarla).
param([int]$Port = 8080)

$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Test-Path ".venv")) {
    python -m venv .venv
    .\.venv\Scripts\python -m pip install -r requirements-dev.txt
}
$env:BENCH_MOCK = "1"
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"
.\.venv\Scripts\python -m uvicorn --factory app.main:create_app --host 127.0.0.1 --port $Port
