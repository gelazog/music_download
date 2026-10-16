"""Historial persistente de descargas.

Su única razón de ser es no bajar dos veces lo mismo. Una entrada se
identifica por (video, formato): el mismo video en MP3 y en MP4 son dos
descargas distintas y ambas son legítimas.

Las entradas guardan la ruta final del archivo. Si el usuario borra o mueve
el archivo, la entrada deja de contar como descargada (``prune_missing``),
así que volver a bajarlo funciona sin tener que vaciar el historial.
"""
import json
import logging
import os
import tempfile
import threading
import time
from pathlib import Path

from .constants import HISTORY_FILE

log = logging.getLogger("ytdown.history")

_MAX_ENTRIES = 20000   # techo defensivo: el JSON no debe crecer sin límite
_SAVE_GAP = 3.0        # segundos mínimos entre escrituras del archivo


def make_key(video_key: str, fmt: str) -> str:
    return f"{video_key}|{fmt}"


class History:
    """Índice {clave: entrada} con escritura atómica y diferida."""

    def __init__(self, path: Path | str = HISTORY_FILE) -> None:
        self._path = Path(path)
        self._lock = threading.RLock()
        self._data: dict[str, dict] = {}
        self._dirty = False
        self._last_save = 0.0
        self._load()

    # ── persistencia ───────────────────────────────────────────────────────────
    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self._data = {k: v for k, v in raw.get("entries", raw).items()
                              if isinstance(v, dict)}
            log.info("Historial cargado: %d descargas registradas", len(self._data))
        except Exception as e:
            log.warning("No se pudo leer el historial (%s); se empieza vacío", e)
            self._data = {}

    def save(self, force: bool = False) -> None:
        """Escritura atómica: primero a un temporal, luego reemplazo.

        Se limita a una escritura cada pocos segundos: al bajar una playlist
        larga, guardar el JSON entero tras cada canción son cientos de
        reescrituras del mismo archivo para nada. Al cerrar se fuerza.
        """
        with self._lock:
            if not self._dirty:
                return
            if not force and time.monotonic() - self._last_save < _SAVE_GAP:
                return
            data = {"version": 1, "entries": self._data}
            self._dirty = False
            self._last_save = time.monotonic()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(self._path.parent),
                                       prefix=".ytdown_hist_", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self._path)
        except Exception as e:
            log.warning("No se pudo guardar el historial: %s", e)

    # ── consultas ──────────────────────────────────────────────────────────────
    def get(self, video_key: str, fmt: str) -> dict | None:
        with self._lock:
            return self._data.get(make_key(video_key, fmt))

    def has(self, video_key: str, fmt: str, *, verify_file: bool = True) -> bool:
        """True si ya se descargó. Con ``verify_file`` comprueba que siga en disco."""
        entry = self.get(video_key, fmt)
        if not entry:
            return False
        if not verify_file:
            return True
        path = entry.get("path")
        if path and not Path(path).exists():
            log.debug("Historial: el archivo de %s ya no existe, se puede rebajar",
                      entry.get("title", "?")[:50])
            return False
        return True

    def formats_of(self, video_key: str) -> list[str]:
        """Formatos en que ya se bajó este video (para mensajes tipo 'ya en MP3')."""
        prefix = video_key + "|"
        with self._lock:
            return [k.split("|", 1)[1] for k in self._data if k.startswith(prefix)]

    def entries(self) -> list[dict]:
        """Todas las entradas, de la más reciente a la más antigua."""
        with self._lock:
            return sorted(self._data.values(),
                          key=lambda e: e.get("ts", 0), reverse=True)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    # ── mutaciones ─────────────────────────────────────────────────────────────
    def add(self, video_key: str, fmt: str, *, title: str = "",
            path: str = "", url: str = "") -> None:
        size = 0
        try:
            if path:
                size = Path(path).stat().st_size
        except OSError:
            pass
        entry = {
            "video": video_key, "fmt": fmt, "title": title,
            "path": path, "url": url, "size": size, "ts": time.time(),
        }
        with self._lock:
            self._data[make_key(video_key, fmt)] = entry
            self._dirty = True
            if len(self._data) > _MAX_ENTRIES:
                self._trim_locked()
        log.debug("Historial + %s [%s]", title[:60], fmt)
        self.save()

    def forget(self, video_key: str, fmt: str | None = None) -> int:
        """Olvida una descarga (o todas las de ese video). Devuelve cuántas quitó."""
        with self._lock:
            if fmt:
                keys = [make_key(video_key, fmt)]
            else:
                keys = [k for k in self._data if k.startswith(video_key + "|")]
            removed = sum(1 for k in keys if self._data.pop(k, None) is not None)
            self._dirty = bool(removed)
        if removed:
            self.save(force=True)
        return removed

    def clear(self) -> int:
        with self._lock:
            n = len(self._data)
            self._data.clear()
            self._dirty = True
        self.save(force=True)
        log.info("Historial vaciado (%d entradas)", n)
        return n

    def prune_missing(self) -> int:
        """Quita las entradas cuyo archivo ya no está en disco."""
        with self._lock:
            gone = [k for k, e in self._data.items()
                    if e.get("path") and not Path(e["path"]).exists()]
            for k in gone:
                del self._data[k]
            self._dirty = bool(gone)
        if gone:
            self.save(force=True)
            log.info("Historial: %d entradas sin archivo fueron depuradas", len(gone))
        return len(gone)

    def _trim_locked(self) -> None:
        """Conserva solo las entradas más recientes. Llamar con el lock tomado."""
        keep = sorted(self._data.items(),
                      key=lambda kv: kv[1].get("ts", 0), reverse=True)[:_MAX_ENTRIES]
        self._data = dict(keep)


# Instancia compartida por toda la app.
shared = History()
