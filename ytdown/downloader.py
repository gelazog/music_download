"""Lógica de descarga: construye opciones yt-dlp y ejecuta descargas."""
import logging
import os
import random
import threading
import time
from typing import Callable

import yt_dlp

from .constants import FFMPEG_PATH, AUDIO_FORMATS
from .cookies import get_cookie_args
from .history import shared as history
from .jsruntime import apply_js_opts, summary as js_summary
from .network import apply_impersonate

log = logging.getLogger("ytdown.downloader")

# Clientes a rotar cuando YouTube rechaza la descarga (bot-check, age-gate,
# 403, sin formatos). android_vr no requiere JS runtime ni PO token; los
# demás aprovechan el runtime JS si está disponible.
_FALLBACK_CLIENTS = ["android_vr", "tv", "web_safari", "mweb"]

# Esperas (segundos) tras un HTTP 429 antes de reintentar el mismo cliente.
_RATE_LIMIT_BACKOFF = (45, 120)

# Perfiles de velocidad. El tiempo muerto entre descargas es lo que más pesa
# en una playlist larga, pero bajarlo del todo aumenta el riesgo de 429.
#   pausa  = espera entre un item y el siguiente
#   sleep  = sleep_interval/max_sleep_interval que yt-dlp aplica por descarga
#   frags  = fragmentos DASH descargados en paralelo
SPEED_PROFILES = {
    "turbo":    {"pausa": (0.2, 0.8), "sleep": (0, 0), "req": 0, "frags": 5},
    "normal":   {"pausa": (1.0, 2.2), "sleep": (0, 1), "req": 0, "frags": 3},
    "seguro":   {"pausa": (2.5, 5.0), "sleep": (2, 5), "req": 1, "frags": 1},
}
DEFAULT_SPEED = "normal"

# Último cliente que funcionó en esta sesión. Si YouTube rechaza al cliente
# por defecto, no tiene sentido volver a intentarlo con cada canción de la
# cola: se arranca directamente por el que sí funcionó.
_preferred_client: str | None = None


def preferred_client() -> str | None:
    return _preferred_client


def _remember_client(client: str | None) -> None:
    global _preferred_client
    if client and client != _preferred_client:
        _preferred_client = client
        log.info("Cliente preferido para los siguientes items: %s", client)


# -- Patch android_vr para que funcione con cookies ----------------------------
# yt-dlp 2026+ salta android_vr si hay cookiefile porque SUPPORTS_COOKIES no
# está en su definición. Pero android_vr sí puede autenticar con cookies para
# age-gate. Añadir el flag evita que yt-dlp lo descarte.
try:
    from yt_dlp.extractor.youtube._base import INNERTUBE_CLIENTS  # type: ignore
    if "android_vr" in INNERTUBE_CLIENTS:
        INNERTUBE_CLIENTS["android_vr"]["SUPPORTS_COOKIES"] = True
        log.debug("Patch android_vr SUPPORTS_COOKIES=True aplicado")
except Exception as _e:
    log.debug("No se pudo patchear android_vr: %s", _e)

_AGE_GATE_SIGNALS = (
    "sign in to confirm your age",
    "age-restricted",
    "age restricted",
    "inappropriate for some users",
)

_LOGIN_REQUIRED_SIGNALS = (
    "please sign in",
    "sign in to",
    "this video is private",
    "members-only",
    "join this channel",
)

_FORMAT_MISSING_SIGNALS = (
    "requested format is not available",
    "only images are available",
    "no video formats found",
)

_BOT_CHECK_SIGNALS = (
    "confirm you're not a bot",
    "confirm you are not a bot",
    "not a robot",
    "captcha",
    "http error 403",
)

_RATE_LIMIT_SIGNALS = (
    "http error 429",
    "too many requests",
)

# Errores definitivos: rotar de cliente no cambia nada.
_PERMANENT_SIGNALS = (
    "video unavailable",
    "this video is not available",
    "has been removed",
    "account associated with this video has been terminated",
    "is not available in your country",
    "private video",
)

_LIVE_SIGNALS = (
    "this live event will begin",
    "premieres in",
    "is not currently live",
)

_FFMPEG_SIGNALS = (
    "ffmpeg not found",
    "ffprobe and ffmpeg not found",
    "you have requested merging",
)

_DISK_SIGNALS = (
    "no space left",
    "errno 28",
    "disk full",
)

_NETWORK_SIGNALS = (
    "temporary failure in name resolution",
    "getaddrinfo failed",
    "connection refused",
    "connection reset",
    "timed out",
    "unable to connect",
)


def _has(msg: str, signals: tuple) -> bool:
    low = msg.lower()
    return any(s in low for s in signals)


def _is_age_gate(msg: str) -> bool:
    return _has(msg, _AGE_GATE_SIGNALS)


def _is_login_required(msg: str) -> bool:
    return _has(msg, _LOGIN_REQUIRED_SIGNALS)


def _is_format_missing(msg: str) -> bool:
    return _has(msg, _FORMAT_MISSING_SIGNALS)


def _is_bot_check(msg: str) -> bool:
    return _has(msg, _BOT_CHECK_SIGNALS)


def _is_rate_limited(msg: str) -> bool:
    return _has(msg, _RATE_LIMIT_SIGNALS)


def _is_permanent(msg: str) -> bool:
    return _has(msg, _PERMANENT_SIGNALS)


def _needs_client_retry(msg: str) -> bool:
    """True si vale la pena reintentar con otro player_client."""
    if _is_permanent(msg) or _has(msg, _LIVE_SIGNALS) or _has(msg, _DISK_SIGNALS):
        return False
    return (_is_bot_check(msg) or _is_age_gate(msg)
            or _is_login_required(msg) or _is_format_missing(msg))


def friendly_error(msg: str) -> str:
    """Convierte errores técnicos de yt-dlp en algo que el usuario pueda accionar."""
    if _has(msg, _DISK_SIGNALS):
        return "No queda espacio en el disco. Libera espacio o cambia la carpeta de destino."
    if _has(msg, _FFMPEG_SIGNALS):
        return ("Falta FFmpeg: sin él no se puede convertir ni unir audio y video. "
                "Ábrelo desde 'Requisitos' e instálalo con un clic.")
    if _has(msg, _LIVE_SIGNALS):
        return "Es un estreno o directo que aún no empezó. Inténtalo cuando termine."
    if _is_permanent(msg):
        return "El video no está disponible (privado, borrado o bloqueado por país)."
    if _is_rate_limited(msg):
        return ("YouTube está limitando tu IP (429). Cambia el modo de velocidad a "
                "'Seguro', o espera unos minutos y reintenta.")
    if _is_bot_check(msg):
        return ("YouTube pidió verificación anti-bot y todos los clientes fallaron. "
                "Espera unos minutos; si persiste, exporta cookies como último recurso.")
    if _is_login_required(msg):
        return ("Este video requiere iniciar sesión. "
                "Exporta las cookies de tu navegador con el botón 'Exportar cookies'.")
    if _is_age_gate(msg):
        return ("Video con restricción de edad. "
                "Exporta las cookies de tu navegador con el botón 'Exportar cookies'.")
    if _is_format_missing(msg):
        return ("No hay formatos disponibles para este video. "
                "Prueba otro formato de salida o exporta cookies.")
    if _has(msg, _NETWORK_SIGNALS):
        return "Sin conexión con YouTube. Revisa tu red y reintenta."

    # Limpiar el prefijo "ERROR: ERROR:" que yt-dlp a veces dobla
    cleaned = msg.replace("ERROR: ERROR:", "ERROR:").strip()
    # Recortar las URLs de ayuda para no saturar la UI
    for cut in ("See  https://", "Use --cookies", "Sign in to confirm"):
        if cut in cleaned:
            cleaned = cleaned.split(cut)[0].strip()
    return cleaned.splitlines()[-1] if cleaned else "Error desconocido"


# Nombre anterior, mantenido por si algo externo lo usaba.
_friendly_error = friendly_error


def build_video_selector(resolution: str = "1080", codec: str = "compatible",
                         with_audio: bool = True) -> str:
    """Selector de formato de yt-dlp para video.

    El `bestvideo+bestaudio` de toda la vida elige lo *mejor por bitrate*, que
    en YouTube hoy es VP9 o AV1 con audio Opus. Metido en un MP4 eso da dos
    problemas clásicos: el video pide instalar códecs y el sonido no se oye,
    porque casi ningún reproductor de Windows descodifica Opus dentro de MP4.

    Por eso "compatible" pide explícitamente avc1 (H.264) y mp4a (AAC), y solo
    si no existen cae hacia lo que haya.
    """
    alto = "" if resolution == "best" else f"[height<={resolution}]"

    if codec == "compatible":
        videos = [f"bestvideo[vcodec^=avc1]{alto}",
                  f"bestvideo[ext=mp4]{alto}",
                  f"bestvideo{alto}"]
        audios = ["bestaudio[acodec^=mp4a]", "bestaudio[ext=m4a]", "bestaudio"]
        progresivo = [f"best[vcodec^=avc1][acodec^=mp4a]{alto}", f"best{alto}"]
    elif codec == "eficiente":
        videos = [f"bestvideo[vcodec^=vp9]{alto}", f"bestvideo{alto}"]
        audios = ["bestaudio[acodec=opus]", "bestaudio"]
        progresivo = [f"best{alto}"]
    else:                                   # máxima calidad: lo que haya
        videos = [f"bestvideo{alto}"]
        audios = ["bestaudio"]
        progresivo = [f"best{alto}"]

    if not with_audio:
        # Solo la pista de imagen. El último recurso lleva audio, pero es
        # preferible eso a no bajar nada si no hay pistas separadas.
        return "/".join(videos + progresivo + ["best"])

    combinaciones = []
    for v in videos:
        for a in audios:
            combinaciones.append(f"{v}+{a}")
    return "/".join(combinaciones + progresivo + ["best"])


def build_ydl_opts(
    url: str,
    folder: str,
    fmt: str,
    quality: str,
    *,
    resolution: str = "1080",
    vcodec: str = "compatible",
    container: str = "mp4",
    with_audio: bool = True,
    force_compat: bool = False,
    playlist: bool = False,
    metadata: bool = True,
    thumbnail: bool = False,
    subtitles: bool = False,
    normalize: bool = False,
    sponsor_block: bool = False,
    embed_chapters: bool = False,
    rate_limit: str = "",
    browser: str = "ninguno",
    speed: str = DEFAULT_SPEED,
    age_bypass: bool = True,
    progress_hook: Callable | None = None,
) -> dict:
    is_audio = fmt in AUDIO_FORMATS
    profile = SPEED_PROFILES.get(speed, SPEED_PROFILES[DEFAULT_SPEED])

    # La carpeta va en `paths`, no incrustada en la plantilla: si la ruta
    # contiene un '%' (perfectamente legal en Windows) yt-dlp la interpretaría
    # como un campo de la plantilla y escribiría en un sitio inesperado.
    tmpl = ("%(playlist_index)02d - %(title)s.%(ext)s" if playlist
            else "%(title)s.%(ext)s")

    opts: dict = {
        "outtmpl":                 tmpl,
        "paths":                   {"home": folder},
        "quiet":                   True,
        "no_warnings":             True,
        "noplaylist":              not playlist,
        # En playlists ignoramos errores individuales para no detener toda la
        # lista. En descargas individuales los mostramos.
        "ignoreerrors":            playlist,
        "sleep_interval":          profile["sleep"][0],
        "max_sleep_interval":      profile["sleep"][1],
        "sleep_interval_requests": profile["req"],
        "retries":                 5,
        "fragment_retries":        5,
        "extractor_retries":       3,
        "http_chunk_size":         10485760,
        # Varios fragmentos DASH a la vez: es la mejora de velocidad más
        # grande en video, y no multiplica las peticiones a la API.
        "concurrent_fragment_downloads": profile["frags"],
        "windowsfilenames":        True,
        "trim_file_name":          150,
        "overwrites":              False,
        "continuedl":              True,
    }

    if FFMPEG_PATH:
        opts["ffmpeg_location"] = FFMPEG_PATH

    if progress_hook:
        opts["progress_hooks"] = [progress_hook]

    # Cookies del navegador
    cookie_opts, cookie_warn = get_cookie_args(browser)
    opts.update(cookie_opts)
    if cookie_warn:
        log.debug("Cookies: %s", cookie_warn)

    # Estrategia anti-bloqueo SIN cookies:
    #  1) Con runtime JS (Deno/Node) + yt-dlp-ejs: dejar los clientes por
    #     defecto de yt-dlp (mantenidos upstream), que resuelven los
    #     challenges de YouTube — máxima compatibilidad y formatos.
    #  2) Sin runtime: forzar android_vr, que no requiere JS ni PO token.
    # En ambos casos, si YouTube rechaza, se rotan clientes (ver _client_ladder).
    if apply_js_opts(opts):
        log.debug("Runtime JS activo (%s) — clientes por defecto de yt-dlp",
                  js_summary())
    else:
        opts["extractor_args"] = {"youtube": {"player_client": ["android_vr"]}}

    # Formato de salida
    if fmt == "mp3":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio",
                                   "preferredcodec": "mp3",
                                   "preferredquality": quality}]
    elif fmt == "m4a":
        opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio",
                                   "preferredcodec": "m4a",
                                   "preferredquality": quality}]
    elif fmt == "opus":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio",
                                   "preferredcodec": "opus",
                                   "preferredquality": quality}]
    elif fmt == "flac":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio",
                                   "preferredcodec": "flac"}]
    elif fmt == "wav":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{"key": "FFmpegExtractAudio",
                                   "preferredcodec": "wav"}]
    else:
        opts["format"] = build_video_selector(resolution, vcodec, with_audio)
        opts["merge_output_format"] = container
        if force_compat:
            # Recodifica de verdad (a MP4 sale H.264 + AAC). Es lento, pero es
            # la única salida cuando YouTube no ofrece H.264 — por encima de
            # 1080p solo hay VP9 y AV1.
            opts.setdefault("postprocessors", []).append(
                {"key": "FFmpegVideoConvertor", "preferedformat": container})
        log.debug("Selector de video: %s -> .%s", opts["format"], container)

    pps = opts.setdefault("postprocessors", [])

    # add_chapters incrusta las marcas de capitulo. Antes aqui habia un
    # FFmpegSplitChapters, que no incrusta nada: trocea la descarga en un
    # archivo por capitulo, que no es lo que promete la casilla.
    if metadata or embed_chapters:
        pps.append({"key": "FFmpegMetadata",
                    "add_metadata": metadata,
                    "add_chapters": embed_chapters,
                    "add_infojson": "if_exists"})
    if thumbnail and is_audio:
        opts["writethumbnail"] = True
        pps.append({"key": "EmbedThumbnail"})
    if subtitles:
        opts["writesubtitles"] = True
        opts["subtitleslangs"] = ["es", "en"]
        opts["embedsubtitles"] = True
    if sponsor_block:
        opts["sponsorblock_remove"] = ["sponsor", "selfpromo", "interaction"]
    if normalize and is_audio:
        opts["postprocessor_args"] = {
            "extractaudio": ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]}
    if rate_limit:
        opts["ratelimit"] = rate_limit

    return opts


def _client_ladder(base_opts: dict) -> list[str | None]:
    """Orden en que se prueban los player_client. None = defaults de yt-dlp."""
    ea = base_opts.get("extractor_args") or {}
    yt = ea.get("youtube") or {}
    explicit = (yt.get("player_client") or [None])[0]

    ladder: list[str | None] = []
    if explicit:                     # sin runtime JS: android_vr ya viene fijado
        ladder.append(explicit)
    else:
        if _preferred_client:        # el que funcionó antes en esta sesión
            ladder.append(_preferred_client)
        ladder.append(None)          # clientes por defecto de yt-dlp
    ladder += [c for c in _FALLBACK_CLIENTS if c not in ladder]
    return ladder


class _YtdlpLogger:
    """Redirige mensajes de yt-dlp al logger de Python."""
    def __init__(self, captured: list[str]):
        self._cap = captured

    def debug(self, msg: str) -> None:
        # yt-dlp prefija mensajes normales con [debug]
        if msg.startswith("[debug]"):
            log.debug("yt-dlp %s", msg)
        else:
            log.info("yt-dlp %s", msg)

    def warning(self, msg: str) -> None:
        log.warning("yt-dlp WARN: %s", msg)
        self._cap.append(msg)

    def error(self, msg: str) -> None:
        log.error("yt-dlp ERR: %s", msg)
        self._cap.append(msg)


class DownloadManager:
    """Ejecuta descargas secuencialmente en un hilo de fondo.

    Los jobs se identifican por ``id`` estable, no por posición: la cola puede
    crecer (playlists expandidas) o encogerse mientras se descarga.

    Cada job es un dict con: id, url, opts y, opcionalmente, key/fmt/title
    (para el historial) y skip_known (para no rebajar lo ya descargado).

    Callbacks (llamados desde el worker — usar .after() para tocar la UI):
        on_progress(d)
        on_item_start(job_id, pos, total)
        on_item_done(job_id, ok, err, info)   info: path, skipped
        on_all_done(resumen)
        on_client_retry(job_id, client)
        on_rate_limit(job_id, segundos)
    """

    def __init__(self) -> None:
        self._thread:      threading.Thread | None = None
        self._stop_event   = threading.Event()
        self._pause_event  = threading.Event()
        self._pause_event.set()
        self.running = False
        self.speed = DEFAULT_SPEED

    def start(self, jobs: list[dict], callbacks: dict,
              speed: str = DEFAULT_SPEED) -> None:
        if self.running:
            log.warning("DownloadManager.start() llamado mientras ya corre — ignorado")
            return
        self._stop_event.clear()
        self._pause_event.set()
        self.running = True
        self.speed = speed if speed in SPEED_PROFILES else DEFAULT_SPEED
        self._thread = threading.Thread(
            target=self._worker, args=(jobs, callbacks), daemon=True,
            name="ytdown-downloader")
        self._thread.start()
        log.info("Descarga iniciada: %d job(s), modo %s", len(jobs), self.speed)

    def pause(self) -> None:
        self._pause_event.clear()
        log.info("Descarga pausada")

    def resume(self) -> None:
        self._pause_event.set()
        log.info("Descarga reanudada")

    def cancel(self) -> None:
        log.info("Descarga cancelada por el usuario")
        self._stop_event.set()
        self._pause_event.set()   # desbloquea si estaba pausado

    @property
    def paused(self) -> bool:
        return not self._pause_event.is_set()

    @property
    def cancelled(self) -> bool:
        return self._stop_event.is_set()

    # -- utilidades de espera --------------------------------------------------
    def _sleep_interruptible(self, secs: float) -> bool:
        """Duerme respetando cancelación. False si se canceló."""
        end = time.time() + secs
        while time.time() < end:
            if self._stop_event.is_set():
                return False
            time.sleep(0.2)
        return not self._stop_event.is_set()

    def _wait_if_paused(self) -> bool:
        while self.paused:
            if self._stop_event.is_set():
                return False
            time.sleep(0.2)
        return not self._stop_event.is_set()

    # -- worker ----------------------------------------------------------------
    def _worker(self, jobs: list[dict], cbs: dict) -> None:
        total = len(jobs)
        summary = {"total": total, "ok": 0, "error": 0,
                   "skipped": 0, "cancelled": False}
        gap = SPEED_PROFILES[self.speed]["pausa"]
        started = time.time()

        for i, job in enumerate(jobs):
            if self._stop_event.is_set():
                break
            if not self._wait_if_paused():
                break

            jid = job.get("id", i)

            # Puede haberse descargado hace un momento: la misma canción sale
            # en dos playlists distintas más a menudo de lo que parece.
            if job.get("skip_known") and job.get("key") and job.get("fmt"):
                if history.has(job["key"], job["fmt"]):
                    summary["skipped"] += 1
                    log.info("[%s] Omitido: ya estaba descargado", jid)
                    if cb := cbs.get("on_item_done"):
                        cb(jid, False, "", {"skipped": True, "path":
                                            (history.get(job["key"], job["fmt"]) or {}).get("path", "")})
                    continue

            if cb := cbs.get("on_item_start"):
                cb(jid, i, total)

            # Pausa anti-rate-limit entre descargas
            if i > 0 and not self._sleep_interruptible(random.uniform(*gap)):
                break

            ok, err, info = self._run_single(job, cbs, jid)
            if ok:
                summary["ok"] += 1
            elif err != "cancelado":
                summary["error"] += 1

            if cb := cbs.get("on_item_done"):
                cb(jid, ok, err, info)

        summary["cancelled"] = self._stop_event.is_set()
        summary["elapsed"] = time.time() - started
        self.running = False
        log.info("Worker terminado: %d ok, %d error, %d omitidos en %.0fs",
                 summary["ok"], summary["error"], summary["skipped"],
                 summary["elapsed"])
        if cb := cbs.get("on_all_done"):
            cb(summary)

    def _make_progress_hook(self, user_hook, sink: dict):
        """Envuelve el hook del usuario añadiendo pausa, cancelación y ruta final."""
        def hook(d):
            while not self._pause_event.is_set():
                if self._stop_event.is_set():
                    break
                time.sleep(0.15)
            if self._stop_event.is_set():
                raise yt_dlp.DownloadError("Cancelado por el usuario")
            if d.get("status") == "finished":
                info = d.get("info_dict") or {}
                path = info.get("filepath") or d.get("filename") or ""
                if path:
                    sink["path"] = path
            if user_hook:
                user_hook(d)
        return hook

    @staticmethod
    def _make_pp_hook(sink: dict):
        """Tras el postproceso, ``filepath`` ya apunta al archivo definitivo.

        Es la única forma fiable de saber dónde quedó el MP3: el nombre que se
        ve durante la descarga es el del .webm intermedio.
        """
        def hook(d):
            if d.get("status") != "finished":
                return
            info = d.get("info_dict") or {}
            path = info.get("filepath") or ""
            if path:
                sink["path"] = path
        return hook

    def _attempt(self, url: str, base: dict, client: str | None, cbs: dict,
                 *, force_ipv4: bool = False) -> tuple[bool, str, bool, dict]:
        """Un intento de descarga. Devuelve (ok, error, fue_429, info)."""
        opts = dict(base)
        sink: dict = {}
        opts["progress_hooks"] = [self._make_progress_hook(cbs.get("on_progress"), sink)]
        opts["postprocessor_hooks"] = [self._make_pp_hook(sink)]
        captured: list[str] = []
        opts["logger"] = _YtdlpLogger(captured)

        if client:
            ea = dict(opts.get("extractor_args") or {})
            ea["youtube"] = {**(ea.get("youtube") or {}), "player_client": [client]}
            opts["extractor_args"] = ea

        # La huella TLS debe ser coherente con el cliente declarado.
        opts.pop("impersonate", None)
        apply_impersonate(opts, client)

        if force_ipv4:
            # Muchos bloqueos de YouTube son por rango IPv6; forzar IPv4 ayuda.
            opts["source_address"] = "0.0.0.0"

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ret = ydl.download([url])
            if self._stop_event.is_set():
                return False, "cancelado", False, sink
            if ret == 0:
                return True, "", False, sink
            msg = "\n".join(captured) or f"yt-dlp salió con código {ret}"
        except Exception as e:
            if self._stop_event.is_set():
                return False, "cancelado", False, sink
            msg = "\n".join([*captured, str(e)])

        return False, msg, _is_rate_limited(msg), sink

    def _finish(self, job: dict, sink: dict) -> dict:
        """Registra la descarga en el historial y devuelve la info del item."""
        path = sink.get("path", "")
        if path and not os.path.exists(path):
            path = ""
        if job.get("key") and job.get("fmt"):
            history.add(job["key"], job["fmt"], title=job.get("title", ""),
                        path=path, url=job.get("url", ""))
        return {"path": path, "skipped": False}

    def _run_single(self, job: dict, cbs: dict, jid: int) -> tuple[bool, str, dict]:
        """Descarga un item probando la escalera de estrategias anti-bloqueo.

        Orden: cliente preferido/por defecto -> rotación de clientes ->
        (ante 429) espera exponencial -> último recurso, IPv4 forzado.
        """
        url  = job["url"]
        base = dict(job["opts"])
        log.info("[%s] Descargando: %s", jid, url)

        ladder = _client_ladder(base)
        last_err = ""

        for n, client in enumerate(ladder):
            if self._stop_event.is_set():
                return False, "cancelado", {}
            if not self._wait_if_paused():
                return False, "cancelado", {}

            if n > 0:
                log.warning("[%s] Reintentando con cliente '%s'...", jid, client or "default")
                if cb := cbs.get("on_client_retry"):
                    cb(jid, client or "default")

            ok, err, limited, sink = self._attempt(url, base, client, cbs)
            if ok:
                _remember_client(client)
                log.info("[%s] Completado OK (cliente %s)", jid, client or "default")
                return True, "", self._finish(job, sink)
            if err == "cancelado":
                return False, "cancelado", {}

            # HTTP 429: no es culpa del cliente, es la IP. Esperar y reintentar.
            for wait in _RATE_LIMIT_BACKOFF:
                if not limited:
                    break
                log.warning("[%s] Rate limit (429). Esperando %ds...", jid, wait)
                if cb := cbs.get("on_rate_limit"):
                    cb(jid, wait)
                if not self._sleep_interruptible(wait):
                    return False, "cancelado", {}
                ok, err, limited, sink = self._attempt(url, base, client, cbs)
                if ok:
                    _remember_client(client)
                    return True, "", self._finish(job, sink)
                if err == "cancelado":
                    return False, "cancelado", {}

            last_err = err
            log.error("[%s] Cliente '%s' falló: %s", jid, client or "default", err[:200])

            if not _needs_client_retry(err):
                # Error definitivo (video borrado, privado...): rotar no ayuda.
                return False, friendly_error(err), {}

        # Último recurso: forzar IPv4 con el cliente más permisivo.
        if not self._stop_event.is_set():
            log.warning("[%s] Último intento: IPv4 forzado + android_vr", jid)
            if cb := cbs.get("on_client_retry"):
                cb(jid, "android_vr (IPv4)")
            ok, err, _limited, sink = self._attempt(url, base, "android_vr", cbs,
                                                    force_ipv4=True)
            if ok:
                log.info("[%s] Completado OK con IPv4 forzado", jid)
                return True, "", self._finish(job, sink)
            if err == "cancelado":
                return False, "cancelado", {}
            last_err = err or last_err

        return False, friendly_error(last_err or "Todos los intentos fallaron"), {}
