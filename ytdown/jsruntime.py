"""Detección de runtimes JavaScript (Deno/Node) para yt-dlp.

Desde yt-dlp 2025.11 se necesita un runtime JS externo para resolver los
"challenges" de YouTube. Con runtime + yt-dlp-ejs, los clientes por defecto
(android_vr, web_safari, tv...) funcionan completos y SIN cookies.
Sin runtime, solo android_vr funciona de forma fiable (menos formatos).

Todo se cachea: construir las opciones de una playlist de 200 videos llamaba
antes a ``shutil.which`` 200 veces, y el resultado no cambia a mitad de sesión.
"""
import logging
import shutil
from functools import lru_cache

from .constants import APP_DIR

log = logging.getLogger("ytdown.jsruntime")


def _find_portable(exe: str) -> str | None:
    cand = APP_DIR / exe
    return str(cand) if cand.exists() else None


@lru_cache(maxsize=1)
def find_deno() -> str | None:
    return _find_portable("deno.exe") or shutil.which("deno")


@lru_cache(maxsize=1)
def find_node() -> str | None:
    return _find_portable("node.exe") or shutil.which("node")


@lru_cache(maxsize=1)
def has_ejs() -> bool:
    """True si el paquete yt-dlp-ejs (scripts de challenges) está instalado."""
    try:
        import yt_dlp_ejs  # noqa: F401
        return True
    except ImportError:
        return False


@lru_cache(maxsize=1)
def _runtimes_cached() -> tuple[tuple[str, str], ...]:
    found = []
    if deno := find_deno():
        found.append(("deno", deno))
    if node := find_node():
        found.append(("node", node))
    return tuple(found)


def get_js_runtimes() -> dict:
    """Dict para el parámetro ``js_runtimes`` de YoutubeDL. Vacío si no hay runtime."""
    return {name: {"path": path} for name, path in _runtimes_cached()}


def refresh() -> None:
    """Olvida lo cacheado. Se llama tras instalar Deno/Node desde la app."""
    for fn in (find_deno, find_node, has_ejs, _runtimes_cached):
        fn.cache_clear()


def apply_js_opts(opts: dict) -> bool:
    """Configura runtime JS y componentes EJS en las opciones de yt-dlp.

    Retorna True si hay runtime disponible (los clientes por defecto de
    yt-dlp pueden resolver challenges y no hace falta forzar android_vr).
    """
    runtimes = get_js_runtimes()
    if not runtimes:
        return False
    opts["js_runtimes"] = runtimes
    if not has_ejs():
        # Sin el paquete local yt-dlp-ejs, permitir bajarlo bajo demanda
        opts["remote_components"] = ["ejs:github", "ejs:npm"]
    return True


def summary() -> str:
    """Resumen corto para logs/statusbar, ej. 'node+ejs' o 'sin JS'."""
    parts = [name for name, _path in _runtimes_cached()]
    if not parts:
        return "sin JS"
    return "+".join(parts) + ("+ejs" if has_ejs() else " (sin ejs)")
