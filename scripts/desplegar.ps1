<#
.SYNOPSIS
    Instala o actualiza el banco en la BeagleBoard con un solo comando, desde la PC.

.DESCRIPTION
    1. Arma un paquete con el código de esta carpeta (incluye los cambios sin commit).
    2. Lo copia a la placa por la red (scp).
    3. Entra por SSH y corre scripts/instalar_todo.sh, que configura todo solo.

    Lo más cómodo es usar desplegar.bat, en la raíz del repositorio:

        .\desplegar.bat                       actualiza la aplicación (unos minutos)
        .\desplegar.bat -ConfigurarClave      una sola vez: entrar sin escribir la contraseña de SSH
        .\desplegar.bat -Completo             placa recién instalada: todo desde cero (horas, en tmux)
        .\desplegar.bat -ActualizarConfig     además usa los chips.yaml / bench.yaml nuevos
        .\desplegar.bat -Placa 192.168.0.27   otra IP (por ejemplo la de la red local)
        .\desplegar.bat -Simular              sólo muestra lo que haría

.NOTES
    Necesita los comandos ssh, scp (cliente OpenSSH de Windows) y git.
#>
param(
    [string]$Placa = "100.99.235.81",
    [string]$Usuario = "ubuntu",
    [switch]$Completo,
    [switch]$ConfigurarClave,
    [switch]$ActualizarConfig,
    [switch]$Simular
)

$ErrorActionPreference = "Stop"
$Repo = Split-Path -Parent $PSScriptRoot
$Destino = "$Usuario@$Placa"

function Paso([string]$texto) { Write-Host ""; Write-Host "==> $texto" -ForegroundColor Green }
function Aviso([string]$texto) { Write-Host "    $texto" -ForegroundColor Yellow }
function Falla([string]$texto) {
    Write-Host ""
    Write-Host "ERROR: $texto" -ForegroundColor Red
    exit 1
}

# Ejecuta un programa externo y corta si falla. Los argumentos van como lista
# (sin comillas dobles adentro: PowerShell 5 no las pasa bien a los programas).
function Correr([string]$programa, [string[]]$argumentos) {
    Write-Host ("    > " + $programa + " " + ($argumentos -join " ")) -ForegroundColor DarkGray
    if ($Simular) { return }
    & $programa @argumentos
    if ($LASTEXITCODE -ne 0) { Falla "$programa terminó con error (código $LASTEXITCODE)." }
}

function Probar-Puerto([string]$equipo, [int]$puerto) {
    $cliente = New-Object System.Net.Sockets.TcpClient
    try {
        $intento = $cliente.BeginConnect($equipo, $puerto, $null, $null)
        return ($intento.AsyncWaitHandle.WaitOne(4000) -and $cliente.Connected)
    } catch {
        return $false
    } finally {
        $cliente.Close()
    }
}

# --- 0. Herramientas de la PC ---------------------------------------------------------
Paso "Revisando la PC"
foreach ($cmd in @("ssh", "scp", "git")) {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        if ($cmd -eq "git") {
            Falla "No se encontró git. Instalá Git para Windows: https://git-scm.com"
        }
        Falla ("No se encontró $cmd. Instalá el cliente OpenSSH: Configuración > Aplicaciones > " +
               "Características opcionales > Cliente OpenSSH.")
    }
}
Write-Host "    ssh, scp y git disponibles."

Paso "Buscando la placa en $Placa"
if (-not (Probar-Puerto $Placa 22)) {
    Falla ("La placa no responde en ${Placa}:22. ¿Está encendida? ¿Tailscale conectado en la PC " +
           "(tailscale status)? Si es la primera instalación, usá la IP de la red local: -Placa 192.168.x.y")
}
Write-Host "    La placa responde."

# --- 1. Clave SSH (opcional, una sola vez) ------------------------------------------------
if ($ConfigurarClave) {
    Paso "Configurando la clave SSH (después no pide la contraseña para entrar)"
    $clave = Join-Path $env:USERPROFILE ".ssh\id_ed25519"
    if (-not (Test-Path "$clave.pub")) {
        New-Item -ItemType Directory -Force (Split-Path $clave) | Out-Null
        Write-Host "    Cuando pregunte por una passphrase, apretá Enter dos veces (sin passphrase)."
        Correr "ssh-keygen" @("-t", "ed25519", "-f", $clave)
    } else {
        Write-Host "    Ya existe $clave.pub"
    }
    Correr "scp" @("$clave.pub", "${Destino}:clave_pc.pub")
    Correr "ssh" @($Destino, ("mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && " +
        "(grep -qxFf ~/clave_pc.pub ~/.ssh/authorized_keys || cat ~/clave_pc.pub >> ~/.ssh/authorized_keys) && " +
        "chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys && rm ~/clave_pc.pub"))
    Write-Host "    Listo: ssh $Destino ya no pide contraseña (sudo sí, por seguridad)."
}

# --- 2. Paquete con el código de esta carpeta ------------------------------------------
Paso "Armando el paquete"
Push-Location $Repo
try {
    $referencia = "HEAD"
    $cambios = git status --porcelain --untracked-files=no
    if ($cambios) {
        # git stash create: un commit temporal con los cambios sin commit (no toca tu carpeta).
        $temporal = (git stash create)
        if ($temporal) {
            $referencia = $temporal.Trim()
            Aviso "Hay cambios sin commit: se incluyen en el paquete."
        }
    }
    $nuevos = git ls-files --others --exclude-standard
    if ($nuevos) {
        Aviso "Archivos nuevos que NO van en el paquete (agregalos con git add si hacen falta):"
        $nuevos | Select-Object -First 8 | ForEach-Object { Aviso "  $_" }
    }
    $paquete = Join-Path $env:TEMP "stm32-bench.tgz"
    Correr "git" @("archive", "--format=tar.gz", "-o", $paquete, $referencia)
    if (-not $Simular) {
        Write-Host ("    {0} ({1:N0} KB)" -f $paquete, ((Get-Item $paquete).Length / 1KB))
    }
} finally {
    Pop-Location
}

# --- 3. Copia a la placa -------------------------------------------------------------------
Paso "Copiando a la placa"
Correr "scp" @($paquete, "${Destino}:stm32-bench.tgz")

# --- 4. Instalación en la placa ----------------------------------------------------------
$extraer = "rm -rf ~/stm32-bench && mkdir ~/stm32-bench && tar -xzf ~/stm32-bench.tgz -C ~/stm32-bench"
$opciones = ""
if ($ActualizarConfig) { $opciones = " --config" }

if ($Completo) {
    Paso "Instalación completa en la placa (horas)"
    Write-Host "    Corre dentro de tmux, en la sesión 'instalar': si se corta la conexión o cerrás esta"
    Write-Host "    ventana, sigue sola. Para volver a mirarla:  ssh $Destino  y después  tmux attach -t instalar"
    Write-Host "    Al principio pide la contraseña de sudo, la de la web (si es la primera vez) y la"
    Write-Host "    autorización de Tailscale. Después ya no hace falta estar."
    $remoto = "$extraer && (command -v tmux >/dev/null || sudo apt-get install -y tmux) && " +
              "tmux new -A -s instalar 'sudo bash ~/stm32-bench/scripts/instalar_todo.sh completo$opciones'"
} else {
    Paso "Instalando la aplicación en la placa"
    Write-Host "    Pide la contraseña de sudo (la del usuario $Usuario de la placa)."
    $remoto = "$extraer && sudo bash ~/stm32-bench/scripts/instalar_todo.sh app$opciones"
}
Correr "ssh" @("-t", $Destino, $remoto)

Paso "Terminado"
Write-Host "    Abrí http://${Placa}:8080 y recargá con Ctrl+F5."
