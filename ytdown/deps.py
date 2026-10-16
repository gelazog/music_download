"""Chequeo de dependencias y ofrecimiento de instalarlas desde la app.

El .bat ya revisa todo antes de arrancar, pero la app puede abrirse por otras
vías (doble clic al .py, acceso directo) o alguien puede desinstalar algo a
mitad de camino. Aquí se detecta qué falta y se ofrece instalarlo con un botón,
en vez de fallar con un traceback.
"""
import logging
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from typing import Callable

from .constants import APP_DIR

log = logging.getLogger("ytdown.deps")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class Dep:
    key:       str
    label:     str
    why:       str                       # para qué sirve, en cristiano
    required:  bool = True
    module:    str = ""                  # dependencia pip: módulo a importar
    pip:       str = ""                  # spec de pip a instalar
    binary:    str = ""                  # dependencia externa: ejecutable a buscar
    winget:    str = ""                  # id de winget para instalarla
    manual:    str = ""                  # instrucción si no hay instalador
    portable:  tuple[str, ...] = field(default_factory=tuple)  # exe junto a la app


DEPS: list[Dep] = [
    Dep("customtkinter", "customtkinter", "La interfaz de la app",
        module="customtkinter", pip="customtkinter"),
    Dep("yt_dlp", "yt-dlp", "El motor de descarga",
        module="yt_dlp", pip="yt-dlp[default]"),
    Dep("ejs", "yt-dlp-ejs", "Resuelve los challenges de YouTube: más formatos",
        required=False, module="yt_dlp_ejs", pip="yt-dlp[default]"),
    Dep("curl_cffi", "curl_cffi", "Imita la huella TLS de un navegador real; "
        "evita buena parte del bot-check",
        required=False, module="curl_cffi", pip="curl_cffi>=0.10,<0.16"),
    Dep("pillow", "Pillow", "Miniaturas y portadas",
        module="PIL", pip="Pillow"),
    Dep("requests", "requests", "Descarga de miniaturas",
        module="requests", pip="requests"),
    Dep("ffmpeg", "FFmpeg", "Convierte y une audio/video. Sin esto no hay MP3 ni MP4",
        binary="ffmpeg", winget="Gyan.FFmpeg", portable=("ffmpeg.exe",),
        manual="Descarga 'release essentials' de gyan.dev/ffmpeg/builds "
               "y copia ffmpeg.exe junto a la app."),
    Dep("jsruntime", "Deno", "Runtime JS: desbloquea todos los formatos sin cookies",
        required=False, binary="deno", winget="DenoLand.Deno",
        portable=("deno.exe", "node.exe"),
        manual="winget install DenoLand.Deno — o copia deno.exe junto a la app."),
]


def _portable_hit(dep: Dep) -> str | None:
    for exe in dep.portable:
        cand = APP_DIR / exe
        if cand.exists():
            return str(cand)
    return None


def is_installed(dep: Dep) -> tuple[bool, str]:
    """(instalada, dónde se encontró)."""
    if dep.module:
        try:
            __import__(dep.module)
            return True, "pip"
        except Exception:
            return False, ""
    if dep.binary:
        if found := _portable_hit(dep):
            return True, found
        # 'jsruntime' se da por satisfecho con deno o con node
        names = [dep.binary] + (["node"] if dep.key == "jsruntime" else [])
        for name in names:
            if path := shutil.which(name):
                return True, path
    return False, ""


def check_all() -> list[dict]:
    """Estado de cada dependencia, en orden de aparición."""
    out = []
    for dep in DEPS:
        ok, where = is_installed(dep)
        out.append({"dep": dep, "ok": ok, "where": where})
    return out


def missing(required_only: bool = False) -> list[Dep]:
    return [s["dep"] for s in check_all()
            if not s["ok"] and (s["dep"].required or not required_only)]


def has_winget() -> bool:
    return shutil.which("winget") is not None


def can_autoinstall(dep: Dep) -> bool:
    """¿Sabemos instalarla sin que el usuario abra un navegador?"""
    if dep.pip:
        return True
    return bool(dep.winget) and has_winget()


def install(dep: Dep, on_line: Callable[[str], None] | None = None) -> tuple[bool, str]:
    """Instala una dependencia. Bloquea: llamar desde un hilo.

    Devuelve (ok, mensaje). El mensaje ya viene listo para mostrar al usuario.
    """
    if dep.pip:
        cmd = [sys.executable, "-m", "pip", "install", "-U",
               "--disable-pip-version-check", dep.pip]
    elif dep.winget and has_winget():
        cmd = ["winget", "install", "-e", "--id", dep.winget,
               "--accept-source-agreements", "--accept-package-agreements"]
    else:
        return False, dep.manual or f"Instala {dep.label} manualmente."

    log.info("Instalando %s: %s", dep.label, " ".join(cmd))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace",
                                creationflags=_NO_WINDOW)
        for line in proc.stdout or []:
            line = line.rstrip()
            if line:
                log.debug("[%s] %s", dep.key, line)
                if on_line:
                    on_line(line)
        code = proc.wait()
    except Exception as e:
        log.warning("Fallo instalando %s: %s", dep.label, e)
        return False, f"No se pudo instalar {dep.label}: {e}"

    if code != 0:
        return False, (f"La instalación de {dep.label} terminó con error. "
                       + (dep.manual or "Revisa la consola para el detalle."))

    ok, _ = is_installed(dep)
    if ok:
        return True, f"{dep.label} instalado ✓"
    # winget suele necesitar que se recargue el PATH: el proceso actual no lo ve
    if dep.winget:
        return True, (f"{dep.label} se instaló, pero hay que reiniciar la app "
                      f"(o la terminal) para que aparezca en el PATH.")
    return False, f"{dep.label} se instaló pero sigue sin poder importarse."


def install_many(deps: list[Dep], on_progress: Callable[[str, bool | None], None],
                 on_done: Callable[[list[tuple[Dep, bool, str]]], None]) -> None:
    """Instala varias dependencias en un hilo. Los callbacks llegan desde el hilo."""
    def worker():
        results = []
        for i, dep in enumerate(deps, 1):
            on_progress(f"[{i}/{len(deps)}] Instalando {dep.label}…", None)
            ok, msg = install(dep)
            on_progress(msg, ok)
            results.append((dep, ok, msg))
        on_done(results)

    threading.Thread(target=worker, daemon=True, name="ytdown-deps").start()
