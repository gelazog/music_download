"""Metadatos de videos y playlists, sin descargar nada."""
import logging
import re

import yt_dlp

from .constants import FORMAT_EXT
from .cookies import get_cookie_args
from .jsruntime import apply_js_opts
from .network import apply_impersonate

log = logging.getLogger("ytdown.info")

_URL_RE = re.compile(r"https?://\S+")

# youtu.be/ID, /watch?v=ID, /shorts/ID, /embed/ID, /live/ID
_VIDEO_ID_RE = re.compile(
    r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})")
_LIST_ID_RE = re.compile(r"[?&]list=([A-Za-z0-9_-]+)")


def video_key(url: str) -> str:
    """Clave de identidad de un enlace, para no encolar el mismo video dos veces.

    youtu.be/XXX y watch?v=XXX son el mismo video aunque la URL difiera. Un
    enlace de playlist sin video se identifica por el ID de la lista.
    """
    if m := _VIDEO_ID_RE.search(url):
        return "v:" + m.group(1)
    if m := _LIST_ID_RE.search(url):
        return "l:" + m.group(1)
    return url.strip().rstrip("/").lower()


def video_id(url: str) -> str:
    m = _VIDEO_ID_RE.search(url)
    return m.group(1) if m else ""


def split_urls(text: str) -> list[str]:
    """Extrae todas las URLs de un texto pegado (una por línea, o mezcladas).

    Ignora duplicados manteniendo el orden. Si el texto no trae ninguna URL
    reconocible, devuelve las líneas no vacías (por si es un ID o una búsqueda).
    """
    found = _URL_RE.findall(text or "")
    if not found:
        found = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]

    seen: set[str] = set()
    urls: list[str] = []
    for u in found:
        u = u.strip().rstrip(",;")
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def fetch(url: str, browser: str = "ninguno", *, full_playlist: bool = False) -> dict:
    """Devuelve el info dict de yt-dlp, o lanza excepción.

    ``full_playlist=True`` lista todos los videos de una playlist (plano,
    sin resolver cada uno) para poder expandirla en la cola.
    """
    opts: dict = {
        "quiet":        True,
        "no_warnings":  True,
        "extract_flat": "in_playlist",
        # Los metadatos son una petición de ida y vuelta: no vale la pena
        # insistir mucho, el video se reintentará igual al descargarlo.
        "retries":             2,
        "extractor_retries":   2,
        "socket_timeout":      15,
    }
    if not full_playlist:
        # Para la vista previa basta el primer item: es mucho más rápido.
        opts["playlist_items"] = "1"

    if not apply_js_opts(opts):
        opts["extractor_args"] = {"youtube": {"player_client": ["android_vr"]}}
    apply_impersonate(opts)

    if browser and browser != "ninguno":
        cookie_opts, cookie_warn = get_cookie_args(browser)
        opts.update(cookie_opts)
        if cookie_warn:
            log.warning("Cookies: %s", cookie_warn)

    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def is_playlist_url(url: str) -> bool:
    return "list=" in url or "/playlist" in url


def is_pure_playlist_url(url: str) -> bool:
    """Enlace que *solo* es una playlist: no apunta a ningún video concreto.

    ``/playlist?list=X`` es pura; ``watch?v=A&list=X`` no lo es, y ahí sí tiene
    sentido preguntarle al usuario si quiere el video suelto o la lista entera.
    """
    return is_playlist_url(url) and not _VIDEO_ID_RE.search(url)


def entry_url(entry: dict) -> str:
    """URL utilizable de una entrada plana de playlist.

    Con ``extract_flat`` yt-dlp a veces devuelve solo el ID: hay que
    reconstruir la URL o la descarga fallaría.
    """
    for field in ("webpage_url", "url"):
        val = entry.get(field) or ""
        if val.startswith("http"):
            return val
    vid = entry.get("id") or ""
    return f"https://www.youtube.com/watch?v={vid}" if vid else ""


def expected_ext(fmt: str) -> str:
    return FORMAT_EXT.get(fmt, "")


def fmt_duration(sec: int | float | None) -> str:
    if not sec:
        return ""
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_size(num: int | float | None) -> str:
    if not num:
        return ""
    val = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if val < 1024 or unit == "GB":
            return f"{val:.0f} {unit}" if unit in ("B", "KB") else f"{val:.1f} {unit}"
        val /= 1024
    return ""
