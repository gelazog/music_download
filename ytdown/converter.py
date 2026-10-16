"""Conversión de audio con FFmpeg.

Independiente de las descargas: sirve para cualquier archivo que ya tengas en
el disco, venga de donde venga. Se le puede dar una lista de archivos o una
carpeta entera.

El progreso real se saca de ``-progress pipe:1``, que FFmpeg va escribiendo
con el tiempo ya procesado. Comparado con la duración total (que da ffprobe)
sale un porcentaje de verdad, no una barra indeterminada.
"""
import logging
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from .constants import FFMPEG_PATH, FFPROBE_PATH

log = logging.getLogger("ytdown.converter")

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass(frozen=True)
class AudioFormat:
    key:      str
    label:    str
    ext:      str
    desc:     str
    lossless: bool = False
    # Portada incrustada: solo estos contenedores la admiten sin pelearse.
    cover:    bool = False


FORMATS: list[AudioFormat] = [
    AudioFormat("mp3",  "MP3",   "mp3",  "Lo abre cualquier cosa", cover=True),
    AudioFormat("m4a",  "M4A",   "m4a",  "AAC · mejor calidad que MP3 al mismo peso",
                cover=True),
    AudioFormat("opus", "Opus",  "opus", "La mejor compresión actual"),
    AudioFormat("ogg",  "OGG",   "ogg",  "Vorbis · libre y muy compatible"),
    AudioFormat("flac", "FLAC",  "flac", "Sin pérdida · ocupa bastante",
                lossless=True, cover=True),
    AudioFormat("wav",  "WAV",   "wav",  "Sin comprimir · para editar", lossless=True),
]

FORMATS_BY_KEY = {f.key: f for f in FORMATS}

BITRATES = ["96", "128", "192", "256", "320"]
DEFAULT_BITRATE = "192"

# Qué archivos se consideran al escanear una carpeta.
AUDIO_EXTS = {
    ".mp3", ".m4a", ".m4b", ".m4r", ".aac", ".opus", ".ogg", ".oga", ".spx",
    ".flac", ".wav", ".wma", ".aiff", ".aif", ".aifc", ".alac", ".ape",
    ".mka", ".mp2", ".mpa", ".ac3", ".eac3", ".dts", ".amr", ".au", ".snd",
    ".caf", ".mpc", ".tta", ".wv", ".dsf", ".dff", ".ra", ".gsm", ".voc",
    ".8svx", ".w64", ".aa", ".aax",
}
VIDEO_EXTS = {
    ".mp4", ".mkv", ".webm", ".avi", ".mov", ".flv", ".m4v", ".ts", ".m2ts",
    ".mts", ".mpg", ".mpeg", ".m2v", ".wmv", ".asf", ".3gp", ".3g2", ".ogv",
    ".vob", ".divx", ".f4v", ".mxf", ".rm", ".rmvb", ".dv", ".m4p",
}

# Contenedores que igual llevan video que solo audio. Una descarga de YouTube
# "solo audio" suele quedar en .webm o .m4a; tratarlos como video a ciegas
# hacía que no aparecieran salvo marcando la casilla de videos.
AMBIGUOUS_EXTS = {".webm", ".mp4", ".mkv", ".mov", ".m4v", ".3gp", ".ts",
                  ".ogv", ".asf", ".flv", ".avi"}

# Códecs que en realidad son una imagen incrustada, no una pista de video.
_COVER_CODECS = {"mjpeg", "png", "bmp", "gif", "webp"}

# Tope de archivos ambiguos a los que se les pregunta a ffprobe. Cada consulta
# es un proceso: con una carpeta enorme no compensa mirarlos todos.
_MAX_PROBES = 400


@dataclass
class ScanResult:
    """Lo que salió de escanear una carpeta, y lo que quedó fuera y por qué."""
    files:   list       # Path, ordenados por nombre
    videos:  int = 0    # medios con video que se descartaron (casilla apagada)
    others:  dict = None   # extensión -> cuántos, de lo que no es audio ni video
    probed:  int = 0    # cuántos se comprobaron con ffprobe

    def __post_init__(self):
        if self.others is None:
            self.others = {}

    @property
    def skipped_others(self) -> int:
        return sum(self.others.values())

    def top_others(self, n: int = 3) -> list[tuple[str, int]]:
        return sorted(self.others.items(), key=lambda kv: -kv[1])[:n]


def ffmpeg_available() -> bool:
    return bool(FFMPEG_PATH)


def has_video_stream(path: str | Path) -> bool:
    """True si el archivo lleva video de verdad (no una simple carátula)."""
    if not FFPROBE_PATH:
        return True        # sin ffprobe no podemos afinar: se asume que sí
    try:
        out = subprocess.check_output(
            [FFPROBE_PATH, "-v", "error", "-select_streams", "v",
             "-show_entries", "stream=codec_name:stream_disposition=attached_pic",
             "-of", "csv=p=0", str(path)],
            text=True, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW,
            timeout=15)
    except Exception as e:
        log.debug("ffprobe no pudo mirar %s: %s", path, e)
        return True
    for linea in out.splitlines():
        if not linea.strip():
            continue
        partes = [x.strip() for x in linea.split(",")]
        codec = partes[0] if partes else ""
        adjunta = partes[1] if len(partes) > 1 else "0"
        if adjunta != "1" and codec not in _COVER_CODECS:
            return True
    return False


def scan_folder(folder: str | Path, *, recursive: bool = True,
                include_video: bool = False,
                on_progress=None) -> ScanResult:
    """Busca archivos convertibles en una carpeta.

    Los contenedores ambiguos (.webm, .mp4…) se le preguntan a ffprobe: si solo
    traen audio entran aunque no se pidan videos, que es lo que uno espera de
    una descarga "solo audio" de YouTube.
    """
    root = Path(folder)
    if not root.is_dir():
        return ScanResult(files=[])

    it = root.rglob("*") if recursive else root.glob("*")
    seguros: list[Path] = []
    dudosos: list[Path] = []
    otros: dict[str, int] = {}
    videos = 0

    for p in it:
        if not p.is_file():
            continue
        ext = p.suffix.lower()
        if ext in AUDIO_EXTS:
            seguros.append(p)
        elif ext in VIDEO_EXTS:
            if include_video:
                seguros.append(p)
            elif ext in AMBIGUOUS_EXTS:
                dudosos.append(p)     # puede ser audio disfrazado: se comprueba
            else:
                videos += 1
        else:
            otros[ext or "(sin extensión)"] = otros.get(ext or "(sin extensión)", 0) + 1

    probados = 0
    if dudosos and FFPROBE_PATH:
        recortados = dudosos[:_MAX_PROBES]
        videos += len(dudosos) - len(recortados)
        if on_progress:
            on_progress(f"Comprobando {len(recortados)} archivo(s) que pueden "
                        f"ser solo audio…")
        # En paralelo: son procesos cortos y esperando E/S la mayor parte.
        with ThreadPoolExecutor(max_workers=4) as pool:
            for p, tiene_video in zip(recortados,
                                      pool.map(has_video_stream, recortados)):
                probados += 1
                if tiene_video:
                    videos += 1
                else:
                    seguros.append(p)
    elif dudosos:
        videos += len(dudosos)

    seguros.sort(key=lambda p: str(p).lower())
    log.info("Escaneo de %s: %d convertibles, %d videos descartados, "
             "%d no-medios, %d comprobados con ffprobe",
             root, len(seguros), videos, sum(otros.values()), probados)
    return ScanResult(files=seguros, videos=videos, others=otros, probed=probados)


def probe(path: str | Path) -> dict:
    """Duración y si el archivo lleva carátula incrustada.

    Las dos cosas en una sola llamada a ffprobe: lanzar un proceso por archivo
    ya cuesta, y hacerlo dos veces por nada se nota al convertir una carpeta.
    """
    info = {"duration": 0.0, "has_cover": False}
    if not FFPROBE_PATH:
        return info
    try:
        out = subprocess.check_output(
            [FFPROBE_PATH, "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            text=True, stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW,
            timeout=25)
        import json
        data = json.loads(out)
    except Exception as e:
        log.debug("ffprobe no pudo leer %s: %s", path, e)
        return info

    try:
        info["duration"] = float(data.get("format", {}).get("duration") or 0)
    except (TypeError, ValueError):
        pass

    for stream in data.get("streams") or []:
        if stream.get("codec_type") != "video":
            continue
        # Una portada es un stream de video "de mentira": o lleva la
        # disposición attached_pic, o su códec es un formato de imagen.
        # Un video de verdad NO cabe como carátula y hace fallar a FFmpeg.
        disp = (stream.get("disposition") or {}).get("attached_pic", 0)
        if disp == 1 or stream.get("codec_name") in _COVER_CODECS:
            info["has_cover"] = True
            break
    return info


def probe_duration(path: str | Path) -> float:
    """Duración en segundos, o 0 si no se puede averiguar."""
    return probe(path)["duration"]


def target_path(src: Path, fmt: AudioFormat, out_dir: Path | None,
                *, keep_tree_from: Path | None = None) -> Path:
    """Dónde debe quedar el archivo convertido.

    Sin carpeta de salida, junto al original. Con ``keep_tree_from`` se
    respeta la estructura de subcarpetas dentro de la carpeta de destino.
    """
    if out_dir is None:
        return src.with_suffix("." + fmt.ext)
    base = Path(out_dir)
    if keep_tree_from:
        try:
            base = base / src.parent.relative_to(keep_tree_from)
        except ValueError:
            pass   # el archivo no colgaba de esa raíz: va al nivel de arriba
    return base / (src.stem + "." + fmt.ext)


def resolve_collision(dst: Path, policy: str) -> Path | None:
    """Aplica la política ante un destino ya existente.

    Devuelve la ruta final, o None si hay que saltarse el archivo.
    policy: 'renombrar' | 'omitir' | 'sobrescribir'
    """
    if not dst.exists():
        return dst
    if policy == "omitir":
        return None
    if policy == "sobrescribir":
        return dst
    stem, ext, parent = dst.stem, dst.suffix, dst.parent
    for n in range(2, 1000):
        cand = parent / f"{stem} ({n}){ext}"
        if not cand.exists():
            return cand
    return None


def build_command(src: Path, dst: Path, fmt: AudioFormat, *,
                  bitrate: str = DEFAULT_BITRATE,
                  normalize: bool = False,
                  keep_cover: bool = True,
                  keep_metadata: bool = True,
                  has_cover: bool = False) -> list[str]:
    """Línea de comandos de FFmpeg para una conversión."""
    cmd = [FFMPEG_PATH or "ffmpeg", "-hide_banner", "-nostdin", "-y",
           "-i", str(src)]

    # La portada solo se arrastra si de verdad hay una (``has_cover``) y el
    # contenedor la admite. Copiar a ciegas el stream de video revienta al
    # convertir un video a MP3: una pista de video no es una carátula.
    if keep_cover and fmt.cover and has_cover:
        cmd += ["-map", "0:a:0", "-map", "0:v:0", "-c:v", "copy",
                "-disposition:v:0", "attached_pic"]
    else:
        cmd += ["-map", "0:a:0", "-vn"]

    if keep_metadata:
        cmd += ["-map_metadata", "0"]
    else:
        cmd += ["-map_metadata", "-1"]

    if normalize:
        cmd += ["-af", "loudnorm=I=-14:TP=-1.5:LRA=11"]

    if fmt.key == "mp3":
        cmd += ["-c:a", "libmp3lame", "-b:a", f"{bitrate}k"]
    elif fmt.key == "m4a":
        cmd += ["-c:a", "aac", "-b:a", f"{bitrate}k"]
    elif fmt.key == "opus":
        cmd += ["-c:a", "libopus", "-b:a", f"{bitrate}k"]
    elif fmt.key == "ogg":
        cmd += ["-c:a", "libvorbis", "-b:a", f"{bitrate}k"]
    elif fmt.key == "flac":
        cmd += ["-c:a", "flac", "-compression_level", "5"]
    elif fmt.key == "wav":
        # WAV no tiene etiquetas de verdad; la portada tampoco cabe.
        cmd += ["-c:a", "pcm_s16le"]

    cmd += ["-progress", "pipe:1", "-nostats", "-loglevel", "error", str(dst)]
    return cmd


_OUT_TIME_RE = re.compile(r"out_time_ms=(\d+)")


def friendly_error(stderr: str) -> str:
    """Traduce el berrinche de FFmpeg a algo accionable."""
    low = (stderr or "").lower()
    if "no such file or directory" in low:
        return "El archivo de origen ya no está donde estaba."
    if "permission denied" in low:
        return "Sin permiso para escribir ahí. Prueba con otra carpeta de destino."
    if "no space left" in low:
        return "No queda espacio en el disco."
    if "unknown encoder" in low:
        return ("Tu FFmpeg no trae el codificador para ese formato. "
                "Instala la versión 'release essentials' desde Requisitos.")
    if "invalid data found" in low or "does not contain any stream" in low:
        return "El archivo está dañado o no contiene audio."
    if "does not exist" in low and "stream map" in low:
        return "El archivo no tiene pista de audio que convertir."
    last = [ln.strip() for ln in (stderr or "").splitlines() if ln.strip()]
    return last[-1][:160] if last else "FFmpeg falló sin decir por qué."


class FileList:
    """Lista de archivos a convertir. Sin nada de tkinter, como la cola."""

    def __init__(self) -> None:
        self._items: list[dict] = []
        self._by_path: dict[str, dict] = {}   # evita meter el mismo dos veces
        self._next_id = 1

    # -- acceso ----------------------------------------------------------------
    @property
    def items(self) -> list[dict]:
        return self._items

    def __len__(self) -> int:
        return len(self._items)

    def is_empty(self) -> bool:
        return not self._items

    def by_id(self, item_id: int) -> dict | None:
        return next((it for it in self._items if it["id"] == item_id), None)

    # -- alta ------------------------------------------------------------------
    def add(self, paths) -> tuple[int, int]:
        """Añade rutas. Devuelve (añadidos, repetidos)."""
        nuevos = repetidos = 0
        for raw in paths:
            path = Path(raw)
            clave = str(path.resolve()).lower()
            if clave in self._by_path:
                repetidos += 1
                continue
            try:
                size = path.stat().st_size
            except OSError:
                size = 0
            item = {
                "id":       self._next_id,
                "src":      str(path),
                "name":     path.name,
                "size":     size,
                "duration": 0.0,
                "selected": True,
                "status":   "pending",
                "detail":   "",
                "error":    "",
                "dst":      "",
                "progress": 0.0,
            }
            self._next_id += 1
            self._items.append(item)
            self._by_path[clave] = item
            nuevos += 1
        return nuevos, repetidos

    # -- baja ------------------------------------------------------------------
    def remove(self, item_id: int) -> bool:
        item = self.by_id(item_id)
        if not item or item["status"] == "converting":
            return False
        self._items.remove(item)
        self._by_path.pop(str(Path(item["src"]).resolve()).lower(), None)
        return True

    def remove_where(self, predicate) -> int:
        victimas = [it["id"] for it in self._items
                    if predicate(it) and it["status"] != "converting"]
        return sum(1 for vid in victimas if self.remove(vid))

    def clear(self) -> None:
        self._items.clear()
        self._by_path.clear()

    # -- selección -------------------------------------------------------------
    def toggle(self, item_id: int) -> None:
        if item := self.by_id(item_id):
            if item["status"] != "converting":
                item["selected"] = not item["selected"]

    def select_all(self, value: bool) -> None:
        for it in self._items:
            if it["status"] != "converting":
                it["selected"] = value

    def selected(self) -> list[dict]:
        return [it for it in self._items
                if it["selected"] and it["status"] in ("pending", "error", "skipped")]

    def counts(self) -> dict:
        c = {"total": len(self._items), "selected": 0, "ok": 0,
             "error": 0, "skipped": 0, "bytes": 0}
        for it in self._items:
            if it["selected"]:
                c["selected"] += 1
            if it["status"] == "ok":
                c["ok"] += 1
            elif it["status"] == "error":
                c["error"] += 1
            elif it["status"] == "skipped":
                c["skipped"] += 1
            c["bytes"] += it["size"]
        return c


class Converter:
    """Convierte una lista de archivos, uno detrás de otro, en un hilo.

    Callbacks (llegan desde el hilo — la UI debe usar .after()):
        on_item_start(job_id, pos, total, nombre)
        on_progress(job_id, pct, segundos_hechos)
        on_item_done(job_id, ok, mensaje, destino)
        on_all_done(resumen)
    """

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self._pause.set()
        self._proc: subprocess.Popen | None = None
        self.running = False

    # -- control ---------------------------------------------------------------
    def start(self, jobs: list[dict], callbacks: dict) -> None:
        if self.running:
            log.warning("Converter.start() mientras ya corre — ignorado")
            return
        self._stop.clear()
        self._pause.set()
        self.running = True
        self._thread = threading.Thread(target=self._worker, args=(jobs, callbacks),
                                        daemon=True, name="ytdown-converter")
        self._thread.start()
        log.info("Conversión iniciada: %d archivo(s)", len(jobs))

    def cancel(self) -> None:
        self._stop.set()
        self._pause.set()
        self._kill_current()
        log.info("Conversión cancelada por el usuario")

    def pause(self) -> None:
        self._pause.clear()

    def resume(self) -> None:
        self._pause.set()

    @property
    def paused(self) -> bool:
        return not self._pause.is_set()

    def _kill_current(self) -> None:
        proc = self._proc
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except Exception as e:
                log.debug("No se pudo matar ffmpeg: %s", e)

    # -- worker ----------------------------------------------------------------
    def _worker(self, jobs: list[dict], cbs: dict) -> None:
        resumen = {"total": len(jobs), "ok": 0, "error": 0, "omitidos": 0,
                   "cancelado": False, "elapsed": 0.0, "bytes": 0}
        inicio = time.time()

        for i, job in enumerate(jobs):
            if self._stop.is_set():
                break
            while self.paused and not self._stop.is_set():
                time.sleep(0.15)
            if self._stop.is_set():
                break

            jid = job["id"]
            src = Path(job["src"])
            if cb := cbs.get("on_item_start"):
                cb(jid, i, len(jobs), src.name)

            ok, msg, dst = self._convert_one(job, cbs)
            if ok:
                resumen["ok"] += 1
                try:
                    resumen["bytes"] += Path(dst).stat().st_size
                except OSError:
                    pass
            elif msg == "omitido":
                resumen["omitidos"] += 1
            elif msg != "cancelado":
                resumen["error"] += 1

            if cb := cbs.get("on_item_done"):
                cb(jid, ok, msg, dst)

        resumen["cancelado"] = self._stop.is_set()
        resumen["elapsed"] = time.time() - inicio
        self.running = False
        log.info("Conversión terminada: %d ok, %d error, %d omitidos en %.0fs",
                 resumen["ok"], resumen["error"], resumen["omitidos"],
                 resumen["elapsed"])
        if cb := cbs.get("on_all_done"):
            cb(resumen)

    def _convert_one(self, job: dict, cbs: dict) -> tuple[bool, str, str]:
        jid = job["id"]
        src = Path(job["src"])
        dst = Path(job["dst"])
        fmt = FORMATS_BY_KEY[job["fmt"]]

        if not src.exists():
            return False, "El archivo ya no existe.", ""

        # Convertir un archivo sobre sí mismo lo destruiría a medio camino.
        if src.resolve() == dst.resolve():
            return False, "omitido", ""

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return False, f"No se pudo crear la carpeta de destino: {e}", ""

        datos = probe(src)
        duracion = job.get("duration") or datos["duration"]
        cmd = build_command(
            src, dst, fmt,
            bitrate=job.get("bitrate", DEFAULT_BITRATE),
            normalize=job.get("normalize", False),
            keep_cover=job.get("keep_cover", True),
            keep_metadata=job.get("keep_metadata", True),
            has_cover=datos["has_cover"])

        log.debug("[%s] %s", jid, " ".join(cmd))
        try:
            self._proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="replace",
                creationflags=_NO_WINDOW)
        except Exception as e:
            self._proc = None
            return False, f"No se pudo lanzar FFmpeg: {e}", ""

        proc = self._proc
        try:
            for line in proc.stdout or []:
                if self._stop.is_set():
                    break
                while self.paused and not self._stop.is_set():
                    time.sleep(0.15)
                if m := _OUT_TIME_RE.search(line):
                    hechos = int(m.group(1)) / 1_000_000
                    pct = min(hechos / duracion, 1.0) if duracion else 0.0
                    if cb := cbs.get("on_progress"):
                        cb(jid, pct, hechos)
            proc.wait(timeout=30)
        except Exception as e:
            log.debug("[%s] lectura de progreso interrumpida: %s", jid, e)
        finally:
            stderr = ""
            try:
                stderr = (proc.stderr.read() if proc.stderr else "") or ""
            except Exception:
                pass
            self._proc = None

        if self._stop.is_set():
            # El archivo a medias no sirve para nada y confunde.
            _borrar_silencioso(dst)
            return False, "cancelado", ""

        if proc.returncode != 0:
            _borrar_silencioso(dst)
            return False, friendly_error(stderr), ""

        if not dst.exists() or dst.stat().st_size == 0:
            _borrar_silencioso(dst)
            return False, "FFmpeg terminó pero no dejó ningún archivo.", ""

        if cb := cbs.get("on_progress"):
            cb(jid, 1.0, duracion)
        return True, "", str(dst)


def _borrar_silencioso(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError as e:
        log.debug("No se pudo borrar el parcial %s: %s", path, e)
