#!/usr/bin/env python3
"""
ytdown v2.3 — punto de entrada

Lo normal es arrancar con `iniciar.bat`, que comprueba los requisitos y
pregunta antes de instalar nada. Este script también se puede ejecutar
directamente: si falta alguna librería de Python, la instala sola.

FFmpeg debe estar junto a este archivo (ffmpeg.exe) o en el PATH.

Recomendado, no obligatorio: Deno o Node.js. yt-dlp los usa para resolver los
retos de YouTube, lo que desbloquea todos los formatos y evita el bot-check
SIN cookies. Sin runtime JS la app usa el cliente android_vr, que funciona
pero ofrece menos formatos.

Uso:  python main1_new.py [--debug]
"""
import os
import sys
from pathlib import Path

# ── ffmpeg / deno portables ────────────────────────────────────────────────────
# El directorio del script va primero en el PATH: yt-dlp encuentra ahí
# ffmpeg.exe (y deno.exe si se copia junto a la app → 100% portable).
_HERE = Path(__file__).parent.resolve()
os.environ["PATH"] = str(_HERE) + os.pathsep + os.environ.get("PATH", "")
sys.path.insert(0, str(_HERE))

# ── Consola en UTF-8 ───────────────────────────────────────────────────────────
# Windows usa cp1252 por defecto y se rompe con "→", "…", acentos, etc.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Dependencias ───────────────────────────────────────────────────────────────
# La lista vive en ytdown/deps.py, que es la misma que usa la ventana de
# "Requisitos" dentro de la app: así no hay dos listas que se desincronicen.
from ytdown.deps import DEPS, install, is_installed

_faltan_criticas = []
for _dep in DEPS:
    if not _dep.pip:
        continue                      # ffmpeg/deno los revisa la app y el .bat
    ok, _ = is_installed(_dep)
    if ok:
        continue
    print(f"Falta {_dep.label} ({_dep.why}). Instalando…")
    ok, msg = install(_dep)
    print(("  " + msg) if msg else "")
    if not ok and _dep.required:
        _faltan_criticas.append(_dep)

if _faltan_criticas:
    print()
    print("No se pudo preparar todo lo necesario:")
    for _dep in _faltan_criticas:
        print(f"  · {_dep.label} — instálalo con:")
        print(f"      {sys.executable} -m pip install \"{_dep.pip}\"")
    print()
    print("También puedes ejecutar iniciar.bat, que lo hace por ti.")
    sys.exit(1)

# ── Registro ───────────────────────────────────────────────────────────────────
import logging

_DEBUG = "--debug" in sys.argv
logging.basicConfig(
    level=logging.DEBUG if _DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
for _noisy in ("PIL", "urllib3", "requests"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logging.getLogger("yt_dlp").setLevel(logging.ERROR)

log = logging.getLogger("ytdown")

# ── Estado del entorno ─────────────────────────────────────────────────────────
from ytdown.jsruntime import get_js_runtimes, summary as js_summary
from ytdown.network import available as tls_available, summary as tls_summary

log.info("ytdown %s iniciando — carpeta: %s", "v2.3", _HERE)
log.info("Runtime JS: %s", js_summary())
log.info("Impersonación TLS: %s", tls_summary())

if not tls_available():
    log.warning(
        "Sin curl_cffi: las peticiones se delatan por su huella TLS y YouTube "
        "bloquea más seguido. Instálalo con:  pip install \"curl_cffi>=0.10,<0.16\"")

if not get_js_runtimes():
    log.warning(
        "Sin Deno/Node: se usará el cliente android_vr (funciona, pero con "
        "menos formatos). Para compatibilidad total:  winget install DenoLand.Deno")
    # Suprimir el aviso repetitivo de yt-dlp sobre el runtime ausente
    import warnings
    warnings.filterwarnings("ignore", message=".*No supported JavaScript runtime.*")

# ── Arranque de la app ─────────────────────────────────────────────────────────
import customtkinter as ctk

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

from ytdown.app import YTDownApp

if __name__ == "__main__":
    app = YTDownApp()
    app.mainloop()
