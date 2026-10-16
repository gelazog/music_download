"""Browser cookie helpers para yt-dlp."""
import logging
import os
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

log = logging.getLogger("ytdown.cookies")

# Averiguar si un navegador está abierto cuesta un `tasklist`, y averiguar las
# cookies cuesta stats de disco. Al expandir una playlist se construyen las
# opciones de cada video, así que sin caché eran cientos de subprocesos
# bloqueando la UI. La respuesta no cambia de un segundo a otro.
_CACHE_TTL = 4.0
_running_cache: dict[str, tuple[float, bool]] = {}
_args_cache: dict[str, tuple[float, tuple[dict, str]]] = {}


def invalidate_caches() -> None:
    """Olvida lo cacheado. Tras exportar cookies o cerrar el navegador."""
    _running_cache.clear()
    _args_cache.clear()

_L = os.environ.get("LOCALAPPDATA", "")
_R = os.environ.get("APPDATA", "")

_CHROMIUM_BASES: dict[str, Path] = {
    "vivaldi":  Path(_L) / "Vivaldi/User Data/Default",
    "chrome":   Path(_L) / "Google/Chrome/User Data/Default",
    "edge":     Path(_L) / "Microsoft/Edge/User Data/Default",
    "brave":    Path(_L) / "BraveSoftware/Brave-Browser/User Data/Default",
    "opera":    Path(_R) / "Opera Software/Opera Stable",
    "operagx":  Path(_R) / "Opera Software/Opera GX Stable",
    "chromium": Path(_L) / "Chromium/User Data/Default",
    "whale":    Path(_L) / "Naver/Naver Whale/User Data/Default",
}

_DETECTION_ORDER = [
    "vivaldi", "chrome", "edge", "brave", "opera", "operagx",
    "chromium", "whale", "firefox",
]

BROWSERS = ["auto"] + _DETECTION_ORDER + ["ninguno"]

# Ruta del archivo Netscape exportado (el único formato que acepta cookiefile)
_NETSCAPE_CACHE: Path = Path(tempfile.gettempdir()) / "ytdown_cookies.txt"
_netscape_for_browser: str = ""


def _profile_exists(browser: str) -> bool:
    if browser == "firefox":
        return (Path(_R) / "Mozilla/Firefox/Profiles").is_dir()
    base = _CHROMIUM_BASES.get(browser)
    return bool(base and base.is_dir())


def detect_browser() -> str | None:
    for name in _DETECTION_ORDER:
        if _profile_exists(name):
            log.debug("Navegador detectado: %s", name)
            return name
    log.warning("No se detectó ningún navegador instalado.")
    return None


def resolve_browser(browser: str) -> str:
    if browser == "auto":
        return detect_browser() or "ninguno"
    return browser


def _find_cookies_db(browser: str) -> Path | None:
    """Encuentra el SQLite de cookies del navegador."""
    if browser == "firefox":
        return None
    base = _CHROMIUM_BASES.get(browser)
    if not base or not base.is_dir():
        return None
    for candidate in [base / "Network" / "Cookies", base / "Cookies"]:
        if candidate.exists():
            return candidate
    return None


def _is_browser_running(browser: str) -> bool:
    """Detecta si el proceso del navegador está corriendo."""
    _PROCESS_NAMES = {
        "vivaldi":  "vivaldi.exe",
        "chrome":   "chrome.exe",
        "edge":     "msedge.exe",
        "brave":    "brave.exe",
        "opera":    "opera.exe",
        "operagx":  "opera.exe",
        "chromium": "chromium.exe",
        "whale":    "whale.exe",
        "firefox":  "firefox.exe",
    }
    proc = _PROCESS_NAMES.get(browser)
    if not proc:
        return False

    if hit := _running_cache.get(browser):
        when, value = hit
        if time.time() - when < _CACHE_TTL:
            return value

    try:
        import subprocess
        out = subprocess.check_output(
            ["tasklist", "/FI", f"IMAGENAME eq {proc}", "/NH"],
            text=True, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        running = proc.lower() in out.lower()
    except Exception:
        running = False

    _running_cache[browser] = (time.time(), running)
    return running


def export_netscape_cookies(browser: str) -> tuple[str | None, str]:
    """
    Lee el SQLite de cookies del navegador (que debe estar CERRADO),
    descifra las cookies con DPAPI, y las exporta a un archivo Netscape .txt
    que yt-dlp puede leer con cookiefile=.

    Retorna (ruta_txt, "") o (None, mensaje_error).
    """
    global _netscape_for_browser

    browser = resolve_browser(browser)
    if not browser or browser == "ninguno":
        return None, ""

    # Verificar si el browser está corriendo
    if _is_browser_running(browser):
        msg = (f"{browser.capitalize()} está abierto. "
               f"Ciérralo completamente y presiona 'Exportar cookies' de nuevo.")
        log.warning(msg)
        return None, msg

    src = _find_cookies_db(browser)
    if not src:
        msg = f"No se encontró el archivo de cookies de {browser}."
        log.error(msg)
        return None, msg

    # Copiar el SQLite (ahora debería funcionar porque el browser está cerrado)
    try:
        tmp_db = tempfile.mktemp(suffix=".sqlite", prefix="ytdown_db_")
        shutil.copy2(str(src), tmp_db)
        log.debug("SQLite copiado: %s -> %s", src, tmp_db)
    except Exception as e:
        msg = f"No se pudo copiar el archivo de cookies: {e}"
        log.error(msg)
        return None, msg

    # Extraer cookies usando yt-dlp internamente (descifra DPAPI)
    try:
        import yt_dlp.cookies as ck
        import yt_dlp

        class _Logger:
            def debug(self, m):   log.debug("yt-dlp: %s", m)
            def warning(self, m): log.warning("yt-dlp: %s", m)
            def error(self, m):   log.error("yt-dlp: %s", m)
            def info(self, m):    log.info("yt-dlp: %s", m)

        jar = ck.extract_cookies_from_browser(browser, logger=_Logger())

        # Guardar como Netscape
        jar.save(_NETSCAPE_CACHE, ignore_discard=True, ignore_expires=True)
        size = _NETSCAPE_CACHE.stat().st_size
        log.info("Cookies exportadas: %s -> %s (%d KB)", browser, _NETSCAPE_CACHE, size // 1024)
        _netscape_for_browser = browser
        return str(_NETSCAPE_CACHE), ""

    except Exception as e:
        msg = f"Error extrayendo cookies de {browser}: {e}"
        log.error(msg)
        return None, msg
    finally:
        try:
            Path(tmp_db).unlink(missing_ok=True)
        except Exception:
            pass


def get_cookie_args(browser: str) -> tuple[dict, str]:
    """
    Retorna (opciones_yt_dlp, advertencia).

    Usa el archivo Netscape pre-exportado si existe y es del browser correcto.
    Si no, devuelve un aviso indicando que hay que exportar primero.
    """
    browser_resolved = resolve_browser(browser)
    if not browser_resolved or browser_resolved == "ninguno":
        return {}, ""

    if hit := _args_cache.get(browser_resolved):
        when, value = hit
        if time.time() - when < _CACHE_TTL:
            opts, warn = value
            return dict(opts), warn

    opts, warn = _cookie_args_uncached(browser_resolved)
    _args_cache[browser_resolved] = (time.time(), (opts, warn))
    return dict(opts), warn


def _cookie_args_uncached(browser_resolved: str) -> tuple[dict, str]:
    if not _profile_exists(browser_resolved):
        warn = f"Navegador '{browser_resolved}' no encontrado en disco."
        log.warning(warn)
        return {}, warn

    # Usar el archivo Netscape si ya fue exportado
    if (_NETSCAPE_CACHE.exists()
            and _netscape_for_browser == browser_resolved
            and _NETSCAPE_CACHE.stat().st_size > 100):
        log.info("Usando cookies exportadas: %s", _NETSCAPE_CACHE)
        return {"cookiefile": str(_NETSCAPE_CACHE)}, ""

    # No hay cookies exportadas aún
    running = _is_browser_running(browser_resolved)
    if running:
        warn = (f"{browser_resolved.capitalize()} está abierto — "
                f"ciérralo y usa el botón 'Exportar cookies' en la app.")
    else:
        warn = (f"Cookies no exportadas. "
                f"Usa el botón 'Exportar cookies' para autenticarte.")
    log.warning("Cookies no disponibles: %s", warn)
    return {}, warn


def cookies_exported() -> bool:
    """True si hay un archivo de cookies válido listo para usar."""
    return (_NETSCAPE_CACHE.exists()
            and _netscape_for_browser != ""
            and _NETSCAPE_CACHE.stat().st_size > 100)


def cleanup() -> None:
    try:
        _NETSCAPE_CACHE.unlink(missing_ok=True)
    except Exception:
        pass
