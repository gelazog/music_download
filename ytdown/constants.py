"""Constantes visuales y ajustes globales de la app."""
from pathlib import Path

# ── Paleta ─────────────────────────────────────────────────────────────────────
ACCENT      = "#E22222"
ACCENT_HOV  = "#C01010"
ACCENT2     = "#2563EB"
ACCENT2_HOV = "#1D4ED8"

BG_DARK     = "#0E0E11"
BG_CARD     = "#17171B"
BG_INPUT    = "#1F1F24"
BG_ROW      = "#131316"
BG_HOVER    = "#24242B"
BORDER      = "#2C2C34"

TEXT_PRI    = "#F2F2F5"
TEXT_SEC    = "#9A9AA6"
TEXT_MUT    = "#61616D"

GREEN       = "#22C55E"
YELLOW      = "#F59E0B"
RED_ERR     = "#EF4444"
ORANGE      = "#F97316"
BLUE        = "#38BDF8"
PURPLE      = "#A78BFA"

# ── Rutas ──────────────────────────────────────────────────────────────────────
APP_DIR      = Path(__file__).parent.parent      # carpeta de la app (junto a ffmpeg.exe)
DOWNLOAD_DIR = Path.home() / "Downloads" / "ytdown"
CONFIG_FILE  = Path.home() / ".ytdown_config.json"
HISTORY_FILE = Path.home() / ".ytdown_history.json"

DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)


# ── FFmpeg incluido ────────────────────────────────────────────────────────────
# Busca: 1) junto a la app (APP_DIR)  2) en el PATH
def _find_exe(name: str) -> str | None:
    candidate = APP_DIR / f"{name}.exe"
    if candidate.exists():
        return str(candidate)
    import shutil
    return shutil.which(name)


def find_ffmpeg() -> str | None:
    return _find_exe("ffmpeg")


def find_ffprobe() -> str | None:
    # ffprobe suele venir en la misma carpeta que ffmpeg: si tenemos uno,
    # buscar el otro al lado antes de recurrir al PATH.
    if direct := _find_exe("ffprobe"):
        return direct
    if FFMPEG_PATH:
        sibling = Path(FFMPEG_PATH).with_name("ffprobe.exe")
        if sibling.exists():
            return str(sibling)
    return None


FFMPEG_PATH = find_ffmpeg()
FFPROBE_PATH = find_ffprobe()

# ── Formatos de salida ─────────────────────────────────────────────────────────
# El video es una sola tarjeta: resolución, códec, contenedor y sonido se
# eligen en su propio panel. Meter cada combinación como tarjeta serían
# decenas de botones.
FORMAT_DEFS = [
    ("mp3",   "MP3",   "♫", "Audio · lo abre todo"),
    ("m4a",   "M4A",   "♫", "Audio · AAC nativo"),
    ("opus",  "Opus",  "♫", "Audio · alta compresión"),
    ("flac",  "FLAC",  "♫", "Audio · sin pérdida"),
    ("wav",   "WAV",   "♫", "Audio · sin comprimir"),
    ("video", "Video", "▶", "Con o sin sonido · MP4/MKV"),
]

AUDIO_FORMATS = {"mp3", "m4a", "opus", "flac", "wav"}
VIDEO_FORMAT = "video"

# ── Opciones de video ──────────────────────────────────────────────────────────
RESOLUTIONS = [
    ("best", "La mejor que haya"),
    ("2160", "2160p (4K)"),
    ("1440", "1440p (2K)"),
    ("1080", "1080p (Full HD)"),
    ("720",  "720p (HD)"),
    ("480",  "480p"),
    ("360",  "360p"),
]
RESOLUTION_LABELS = dict(RESOLUTIONS)

# YouTube solo ofrece H.264 hasta 1080p. Por encima únicamente hay VP9 y AV1,
# que es justo lo que obliga a instalar códecs y deja el sonido mudo al
# meterlo en un MP4.
MAX_H264_HEIGHT = 1080

VIDEO_CODECS = [
    ("compatible", "Compatible",
     "H.264 + AAC · se ve y se oye en cualquier reproductor"),
    ("eficiente", "Eficiente",
     "VP9 + Opus · menos peso, pide un reproductor moderno"),
    ("maxima", "Máxima calidad",
     "Lo mejor que exista, aunque sea AV1"),
]
VIDEO_CODEC_LABELS = {k: lbl for k, lbl, _d in VIDEO_CODECS}
VIDEO_CODEC_HELP = {k: d for k, _lbl, d in VIDEO_CODECS}

CONTAINERS = [
    ("mp4", "MP4", "El más compatible"),
    ("mkv", "MKV", "Admite cualquier códec sin quejarse"),
    ("webm", "WEBM", "Solo VP9/AV1 + Opus"),
]
CONTAINER_LABELS = {k: lbl for k, lbl, _d in CONTAINERS}

# Extensión final en disco por formato de audio.
FORMAT_EXT = {
    "mp3": "mp3", "m4a": "m4a", "opus": "opus", "flac": "flac", "wav": "wav",
}

# Etiqueta corta de cada formato, para mensajes y para el historial.
FORMAT_LABEL = {val: label for val, label, _ico, _desc in FORMAT_DEFS}

# Formatos de versiones anteriores de la app -> (formato, resolución).
# Sin esto, una config guardada con "mp4_1080" dejaría el selector en blanco.
LEGACY_FORMATS = {
    "mp4_720":  ("video", "720"),
    "mp4_1080": ("video", "1080"),
    "mp4_best": ("video", "best"),
}


def video_fmt_key(resolution: str, container: str, with_audio: bool = True) -> str:
    """Clave de formato de un video, para la cola y el historial.

    Incluye resolución y contenedor a propósito: bajarse el mismo video en
    720p y en 1080p son dos descargas distintas, y el historial no debe
    tomarlas por repetidas.
    """
    clave = f"video_{resolution}_{container}"
    return clave if with_audio else clave + "_mudo"


def pretty_format(fmt: str) -> str:
    """Nombre legible de un formato, incluidas las claves compuestas de video."""
    if fmt in FORMAT_LABEL:
        return FORMAT_LABEL[fmt]
    if fmt in LEGACY_FORMATS:
        _f, res = LEGACY_FORMATS[fmt]
        return f"Video {RESOLUTION_LABELS.get(res, res)}"
    if fmt.startswith("video_"):
        partes = fmt.split("_")
        res = RESOLUTION_LABELS.get(partes[1], partes[1]) if len(partes) > 1 else ""
        cont = CONTAINER_LABELS.get(partes[2], partes[2].upper()) if len(partes) > 2 else ""
        mudo = "  ·  sin sonido" if fmt.endswith("_mudo") else ""
        return f"Video {res} {cont}{mudo}".strip()
    return fmt


def is_video_fmt(fmt: str) -> bool:
    return fmt == VIDEO_FORMAT or fmt.startswith("video_") or fmt.startswith("mp4_")
