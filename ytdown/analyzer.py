"""Analizador de metadatos en segundo plano.

Los items entran a la cola sin título ("Analizando...") y este módulo los va
resolviendo mientras las descargas siguen corriendo. Así se puede pegar una
lista larga de enlaces y empezar a descargar al instante.

Varios hilos analizan a la vez, pero todos pasan por la misma "puerta" que
espacia las peticiones: lo que dispara el bot-check de YouTube son las
ráfagas, no la concurrencia en sí. Con 3 hilos y 0,45 s entre peticiones se
aprovecha la latencia de red sin amontonarlas.
"""
import logging
import queue
import random
import threading
import time
from typing import Callable

from .info import entry_url, fetch as fetch_info

log = logging.getLogger("ytdown.analyzer")

_WORKERS = 3          # hilos analizando en paralelo
_MIN_GAP = 0.45       # separación mínima entre peticiones (segundos)
_JITTER = 0.35        # aleatoriedad añadida, para no parecer un reloj


class _Gate:
    """Espaciador global de peticiones compartido por todos los hilos."""

    def __init__(self, gap: float = _MIN_GAP) -> None:
        self._gap = gap
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next - now)
            self._next = max(now, self._next) + self._gap + random.uniform(0, _JITTER)
        if delay:
            time.sleep(delay)


class Analyzer:
    """Cola de análisis con varios hilos trabajadores.

    Callbacks (invocados desde los hilos — la UI debe usar .after()):
        on_result(item_id, data)             data: title, channel, duration, thumb
        on_expand(item_id, entries, title)   entradas de una playlist a expandir
        on_error(item_id, msg)
        is_alive(item_id) -> bool            opcional: False si el item ya no existe
    """

    def __init__(self, callbacks: dict | None = None,
                 workers: int = _WORKERS) -> None:
        self._q: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._cbs = callbacks or {}
        self._workers = max(1, workers)
        self._gate = _Gate()
        self._lock = threading.Lock()
        self._inflight = 0

    def set_callbacks(self, callbacks: dict) -> None:
        self._cbs = callbacks

    def submit(self, item_id: int, url: str, browser: str = "ninguno",
               expand_playlist: bool = False) -> None:
        """Encola un item para análisis."""
        self._q.put((item_id, url, browser, expand_playlist))
        self._ensure_running()

    @property
    def pending(self) -> int:
        with self._lock:
            return self._q.qsize() + self._inflight

    @property
    def busy(self) -> bool:
        return self.pending > 0

    def _ensure_running(self) -> None:
        self._threads = [t for t in self._threads if t.is_alive()]
        if len(self._threads) >= self._workers:
            return
        self._stop.clear()
        while len(self._threads) < self._workers:
            t = threading.Thread(target=self._worker, daemon=True,
                                 name=f"ytdown-analyzer-{len(self._threads)}")
            t.start()
            self._threads.append(t)
        log.debug("Analizador: %d hilo(s) activos", len(self._threads))

    def stop(self) -> None:
        self._stop.set()

    def drop_pending(self) -> int:
        """Descarta lo que quede por analizar (al vaciar la cola, por ejemplo)."""
        dropped = 0
        while True:
            try:
                self._q.get_nowait()
                self._q.task_done()
                dropped += 1
            except queue.Empty:
                break
        return dropped

    # -- worker ----------------------------------------------------------------
    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                item_id, url, browser, expand = self._q.get(timeout=1.5)
            except queue.Empty:
                # Sin trabajo pendiente: el hilo termina y se recrea al encolar
                return

            if self._stop.is_set():
                self._q.task_done()
                return

            # Si el usuario ya quitó el item de la cola, no gastamos la petición
            alive = self._cbs.get("is_alive")
            if alive and not alive(item_id):
                log.debug("Item %d ya no está en la cola: análisis omitido", item_id)
                self._q.task_done()
                continue

            with self._lock:
                self._inflight += 1
            try:
                self._gate.wait()
                if not self._stop.is_set():
                    self._analyze_one(item_id, url, browser, expand)
            except Exception as e:
                # Un item raro no puede llevarse por delante el hilo: si muere,
                # los siguientes se quedan colgados en "analizando" para siempre.
                log.exception("Análisis del item %d reventó", item_id)
                if cb := self._cbs.get("on_error"):
                    cb(item_id, str(e))
            finally:
                with self._lock:
                    self._inflight -= 1
                self._q.task_done()

    def _analyze_one(self, item_id: int, url: str, browser: str,
                     expand: bool) -> None:
        log.debug("Analizando item %d: %s", item_id, url)
        started = time.monotonic()
        try:
            info = fetch_info(url, browser, full_playlist=expand)
        except Exception as e:
            msg = str(e)
            log.warning("Análisis falló (item %d): %s", item_id, msg[:160])
            if cb := self._cbs.get("on_error"):
                cb(item_id, msg)
            return

        if not info:
            # yt-dlp devuelve None en vez de lanzar en algunos casos. Sin esto,
            # el item se quedaba en "analizando" y nadie volvía a tocarlo.
            log.warning("Análisis del item %d sin datos (yt-dlp devolvió None)",
                        item_id)
            if cb := self._cbs.get("on_error"):
                cb(item_id, "YouTube no devolvió información de este enlace.")
            return

        is_playlist = info.get("_type") == "playlist"
        log.debug("Item %d resuelto en %.1fs", item_id, time.monotonic() - started)

        if is_playlist and expand:
            entries = []
            for e in (info.get("entries") or []):
                if not e:
                    continue
                url_e = entry_url(e)
                if not url_e:
                    continue
                entries.append({
                    "url":      url_e,
                    "title":    e.get("title") or "Sin título",
                    "duration": e.get("duration") or 0,
                })
            title = info.get("title") or "Playlist"
            log.info("Playlist '%s' expandida en %d items", str(title)[:50], len(entries))
            if cb := self._cbs.get("on_expand"):
                cb(item_id, entries, title)
            return

        data = {
            "title":    info.get("title") or "Sin título",
            "channel":  info.get("channel") or info.get("uploader") or "",
            "duration": info.get("duration") or 0,
            "count":    (info.get("playlist_count") or 0) if is_playlist else 0,
            "thumb":    (info.get("thumbnail")
                         or (info.get("thumbnails") or [{}])[-1].get("url", "")),
        }
        if cb := self._cbs.get("on_result"):
            cb(item_id, data)
