"""Carga y guardado de la configuración persistente."""
import json
import logging

from .constants import CONFIG_FILE, DOWNLOAD_DIR

log = logging.getLogger("ytdown.config")

_DEFAULTS = {
    "folder":               str(DOWNLOAD_DIR),
    "quality":              "192",
    "format":               "mp3",
    # Sin cookies por defecto: la estrategia runtime JS + rotación de
    # clientes cubre la mayoría de videos. Cookies solo para privados/edad.
    "cookies_from_browser": "ninguno",
    "normalize_audio":      False,
    "sponsor_block":        False,
    "embed_chapters":       False,
    "rate_limit":           "",
    "theme":                "dark",
    "last_update_check":    0,
    # Evitar repetidos: no volver a descargar lo que ya está en el historial.
    "skip_downloaded":      True,
    # Los duplicados detectados entran a la cola desmarcados en vez de no
    # entrar: así se ven y el usuario puede forzarlos si de verdad los quiere.
    "show_duplicates":      True,
    # turbo / normal / seguro — ver SPEED_PROFILES en downloader.py
    "speed":                "normal",
    # Expandir automáticamente los grupos de playlist al añadirlos
    "auto_expand":          False,

    # ── Video ─────────────────────────────────────────────────────────────
    # "compatible" por defecto: H.264 + AAC. Lo que YouTube da por defecto
    # (VP9/AV1 + Opus) obliga a instalar códecs y deja el video sin sonido
    # en la mayoría de reproductores de Windows.
    "video_resolution":    "1080",
    "video_codec":         "compatible",
    "video_container":     "mp4",
    "video_sound":         True,
    "video_force_compat":  False,

    # ── Conversor de audio ────────────────────────────────────────────────
    "convert_format":      "mp3",
    "convert_bitrate":     "192",
    "convert_normalize":   False,
    "convert_metadata":    True,
    "convert_cover":       True,
    # "misma" = junto a cada original; "carpeta" = todo a una carpeta fija
    "convert_dest_mode":   "misma",
    "convert_out_folder":  "",
    "convert_keep_tree":   True,
    "convert_collision":   "renombrar",
    "convert_recursive":   True,
    "convert_from_video":  False,
}


def load() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {**_DEFAULTS, **data}
        except Exception as e:
            log.warning("Config ilegible (%s); se usan los valores por defecto", e)
    return dict(_DEFAULTS)


def save(data: dict) -> None:
    merged = {**_DEFAULTS, **data}
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.warning("No se pudo guardar la config: %s", e)
