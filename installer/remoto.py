"""Instalar en una placa por SSH.

La instalación corre en la placa, separada de la conexión (setsid + nohup): si se
cierra el instalador o se corta la red, sigue sola. La salida va a
~/banco-instalar.log, que el instalador va leyendo; al terminar se agrega
"[banco] FIN <código>".

Las contraseñas nunca van en la línea de comandos (se verían con `ps`):
* la de sudo entra por la entrada estándar (`sudo -S`);
* la de la web va en un archivo con permisos 600 que instalar_todo.sh lee y borra.
"""
from __future__ import annotations

import re
import shlex
import socket
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

APP_DIR = "banco-remoto"
LOG_NAME = "banco-instalar.log"
CRED_NAME = ".banco-instalar-cred"

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_STEP = re.compile(r"={5,} (.+?) ={5,}")
_TAILSCALE = re.compile(r"https://login\.tailscale\.com/\S+")
_FIN = re.compile(r"^\[banco\] FIN (\d+)\s*$", re.M)


def _sh(value: str) -> str:
    """Comillas simples de shell (siempre, aunque no hagan falta)."""
    return "'" + value.replace("'", "'\\''") + "'"


def credentials_content(user: str, password: str) -> str:
    """Archivo que instalar_todo.sh --credenciales lee (con `.`) y borra."""
    return f"ADMIN_USER={_sh(user)}\nADMIN_PASSWORD={_sh(password)}\n"


def install_command(home: str, mode: str, prebuilt: Optional[str], with_gdb: bool, update_config: bool,
                    credentials_file: Optional[str]) -> str:
    """Comando que lanza instalar_todo.sh como root, separado de la sesión SSH."""
    args = ["bash", "scripts/instalar_todo.sh", mode]
    if update_config:
        args.append("--config")
    if prebuilt:
        args += ["--binarios", prebuilt]
    if not with_gdb:
        args.append("--sin-gdb")
    if credentials_file:
        args += ["--credenciales", credentials_file]
    inner = " ".join(shlex.quote(a) for a in args) + '; echo "[banco] FIN $?"'
    log = f"{home}/{LOG_NAME}"
    middle = (f"cd {shlex.quote(home + '/' + APP_DIR)} && "
              f"(setsid nohup bash -c {shlex.quote(inner)} > {shlex.quote(log)} 2>&1 < /dev/null &)")
    return "sudo -S -p '' bash -c " + shlex.quote(middle)


@dataclass
class LogProgress:
    step: Optional[str]
    tailscale_url: Optional[str]
    finished: Optional[int]            # código de salida, o None si sigue


def parse_log(text: str) -> LogProgress:
    clean = _ANSI.sub("", text)
    steps = _STEP.findall(clean)
    urls = _TAILSCALE.findall(clean)
    fin = _FIN.findall(clean)
    return LogProgress(step=steps[-1].strip() if steps else None, tailscale_url=urls[-1] if urls else None,
                       finished=int(fin[-1]) if fin else None)


# --------------------------------------------------------------------------- SSH

class RemoteError(RuntimeError):
    pass


class Remote:
    """Una conexión SSH a la placa (paramiko)."""

    def __init__(self, host: str, user: str, password: Optional[str] = None, port: int = 22) -> None:
        import paramiko
        self.host, self.user, self.port = host, user, port
        self.client = paramiko.SSHClient()
        known = Path.home() / ".ssh" / "known_hosts"
        try:
            self.client.load_host_keys(str(known))
        except OSError:
            pass
        # Primera vez con esta placa: se acepta su clave y se guarda (como `ssh` la primera vez).
        # Si después cambia, paramiko lo rechaza (BadHostKeyException).
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            self.client.connect(host, port=port, username=user, password=password or None, timeout=10,
                                banner_timeout=20, auth_timeout=20, look_for_keys=True, allow_agent=True)
        except paramiko.BadHostKeyException:
            raise RemoteError(f"La clave SSH de {host} cambió desde la última vez. Si reinstalaste el sistema "
                              f"de la placa, borrá esa línea de {known} y probá de nuevo.")
        except paramiko.AuthenticationException:
            raise RemoteError("Usuario o contraseña de SSH incorrectos.")
        except (socket.timeout, OSError) as exc:
            raise RemoteError(f"No se pudo conectar a {host}:{port} ({exc}). ¿Está encendida y en la red?")
        try:
            known.parent.mkdir(parents=True, exist_ok=True)
            self.client.save_host_keys(str(known))
        except OSError:
            pass

    def close(self) -> None:
        self.client.close()

    def run(self, command: str, stdin_data: Optional[str] = None, timeout: float = 120) -> Tuple[int, str, str]:
        stdin, stdout, stderr = self.client.exec_command(command, timeout=timeout)
        if stdin_data is not None:
            stdin.write(stdin_data)
            stdin.flush()
        stdin.channel.shutdown_write()
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        return stdout.channel.recv_exit_status(), out, err

    def probe(self) -> Dict[str, str]:
        from .perfil import PROBE_SCRIPT, parse
        code, out, err = self.run("bash -s", stdin_data=PROBE_SCRIPT, timeout=60)
        info = parse(out)
        if not info.get("arch"):
            raise RemoteError("La placa no respondió como se esperaba: " + (err.strip() or out.strip())[:200])
        return info

    def check_sudo(self, password: str) -> None:
        code, _, err = self.run("sudo -S -p '' -v", stdin_data=password + "\n", timeout=30)
        if code != 0:
            raise RemoteError("sudo no aceptó la contraseña (o el usuario no puede usar sudo).")

    def upload(self, local: Path, remote: str,
               progress: Optional[Callable[[int, int], None]] = None) -> None:
        sftp = self.client.open_sftp()
        try:
            sftp.put(str(local), remote, callback=progress)
        finally:
            sftp.close()

    def write_private(self, remote: str, content: str) -> None:
        """Archivo que sólo puede leer el usuario (y root)."""
        sftp = self.client.open_sftp()
        try:
            with sftp.open(remote, "w") as fh:
                fh.chmod(stat.S_IRUSR | stat.S_IWUSR)
                fh.write(content)
        finally:
            sftp.close()

    def read_from(self, remote: str, offset: int) -> str:
        """Lo que se agregó a un archivo desde `offset` bytes ("" si todavía no existe)."""
        sftp = self.client.open_sftp()
        try:
            with sftp.open(remote, "r") as fh:
                fh.seek(offset)
                return fh.read().decode("utf-8", "replace")
        except IOError:
            return ""
        finally:
            sftp.close()


def start_install(remote: Remote, home: str, package: Path, sudo_password: str, mode: str,
                  prebuilt: Optional[str], with_gdb: bool, update_config: bool,
                  web_user: Optional[str] = None, web_password: Optional[str] = None,
                  log: Callable[[str], None] = print) -> str:
    """Copia la aplicación, deja las credenciales y lanza la instalación. Devuelve la ruta del log."""
    remote.check_sudo(sudo_password)
    log("Copiando la aplicación a la placa...")
    tgz = f"{home}/{APP_DIR}.tgz"
    remote.upload(package, tgz)
    code, _, err = remote.run(f"rm -rf {shlex.quote(home + '/' + APP_DIR)} && mkdir -p {shlex.quote(home + '/' + APP_DIR)}"
                              f" && tar -xzf {shlex.quote(tgz)} -C {shlex.quote(home + '/' + APP_DIR)}"
                              f" && rm -f {shlex.quote(tgz)} {shlex.quote(home + '/' + LOG_NAME)}")
    if code != 0:
        raise RemoteError("No se pudo descomprimir la aplicación en la placa: " + err.strip()[:200])
    cred = None
    if web_user and web_password:
        cred = f"{home}/{CRED_NAME}"
        remote.write_private(cred, credentials_content(web_user, web_password))
    log("Lanzando la instalación en la placa...")
    cmd = install_command(home, mode, prebuilt, with_gdb, update_config, cred)
    code, _, err = remote.run(cmd, stdin_data=sudo_password + "\n", timeout=60)
    if code != 0:
        raise RemoteError("No se pudo lanzar la instalación: " + err.strip()[:200])
    return f"{home}/{LOG_NAME}"
