"""Auto-actualización de yt-dlp.

YouTube cambia constantemente; la defensa #1 contra bloqueos es tener el
yt-dlp más reciente. Se actualiza como máximo una vez cada 24 h, en un hilo
de fondo, sin bloquear la UI. Se instala ``yt-dlp[default]`` para incluir
también yt-dlp-ejs (scripts que resuelven los challenges de YouTube).
"""
import logging
import subprocess
import sys
import threading
import time
from typing import Callable

log = logging.getLogger("ytdown.updater")

CHECK_INTERVAL = 24 * 3600  # segundos entre chequeos

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _current_version() -> str:
    import yt_dlp
    return yt_dlp.version.__version__


def _installed_version() -> str:
    """Versión en disco (puede diferir de la importada tras un upgrade)."""
    out = subprocess.check_output(
        [sys.executable, "-c", "import yt_dlp; print(yt_dlp.version.__version__)"],
        text=True, creationflags=_NO_WINDOW)
    return out.strip()


def update_in_background(cfg: dict, save_cfg: Callable[[dict], None],
                         on_status: Callable[[str, bool | None], None] | None = None,
                         ) -> None:
    """Actualiza yt-dlp[default] en un hilo si pasaron >24 h del último chequeo.

    ``on_status(msg, ok)`` se llama desde el hilo — la UI debe usar .after().
    """
    last = float(cfg.get("last_update_check", 0) or 0)
    if time.time() - last < CHECK_INTERVAL:
        log.debug("Chequeo de actualización omitido (último hace %.1f h)",
                  (time.time() - last) / 3600)
        return

    def worker():
        old = _current_version()
        log.info("Buscando actualización de yt-dlp (actual: %s)…", old)
        if on_status:
            on_status("Buscando actualización de yt-dlp…", None)
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "-U", "-q",
                 "--disable-pip-version-check", "yt-dlp[default]"],
                creationflags=_NO_WINDOW)
        except Exception as e:
            log.warning("No se pudo actualizar yt-dlp: %s", e)
            if on_status:
                on_status("", False)
            return

        cfg["last_update_check"] = time.time()
        try:
            save_cfg(cfg)
        except Exception as e:
            log.warning("No se pudo guardar config: %s", e)

        try:
            new = _installed_version()
        except Exception:
            new = old
        if new != old:
            log.info("yt-dlp actualizado %s → %s — reinicia la app para aplicarlo", old, new)
            if on_status:
                on_status(f"yt-dlp {new} instalado — reinicia la app para usarlo", True)
        else:
            log.info("yt-dlp ya está al día (%s)", old)
            if on_status:
                on_status("", False)

    threading.Thread(target=worker, daemon=True, name="ytdlp-updater").start()
