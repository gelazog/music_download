"""Impersonación TLS para evitar el bot-check de YouTube.

YouTube no solo mira el User-Agent: analiza la huella TLS/HTTP2 (JA3) del
cliente. Python se delata al instante. Con curl_cffi, yt-dlp imita la huella
exacta de un Chrome/Safari real, que es una de las señales anti-bot más
fuertes — y no requiere cookies.
"""
import logging

log = logging.getLogger("ytdown.network")

# Target de impersonación preferido por player_client de YouTube.
# El navegador imitado debe coincidir con el cliente que se declara, o la
# incoherencia (cliente Safari + huella Chrome) delata la petición.
_CLIENT_TARGETS = {
    "web_safari": ("safari", "chrome"),
    "web":        ("chrome", "safari"),
    "mweb":       ("chrome", "safari"),
    "tv":         ("chrome", "safari"),
    "tv_simply":  ("chrome", "safari"),
    "ios":        ("safari", "chrome"),
    # Los clientes android_* hablan con la API InnerTube, no con el sitio web;
    # imitar un navegador de escritorio ahí sería incoherente.
    "android":    (),
    "android_vr": (),
}

_DEFAULT_PREFS = ("chrome", "safari")

_targets_cache: list | None = None


def _available_targets() -> list:
    """Lista de ImpersonateTarget soportados (cacheada). Vacía si no hay curl_cffi."""
    global _targets_cache
    if _targets_cache is not None:
        return _targets_cache
    try:
        from yt_dlp import YoutubeDL
        with YoutubeDL({"quiet": True, "no_warnings": True}) as ydl:
            _targets_cache = [t for t, _ in ydl._get_available_impersonate_targets()]
    except Exception as e:
        log.debug("Impersonación no disponible: %s", e)
        _targets_cache = []
    if _targets_cache:
        log.debug("Targets de impersonación: %d disponibles", len(_targets_cache))
    return _targets_cache


def available() -> bool:
    return bool(_available_targets())


def pick_target(client: str | None = None):
    """Mejor ImpersonateTarget para un player_client, o None si no aplica."""
    targets = _available_targets()
    if not targets:
        return None
    prefs = _CLIENT_TARGETS.get(client or "", _DEFAULT_PREFS)
    for name in prefs:
        for t in targets:
            if t.client == name:
                return t
    return None


def apply_impersonate(opts: dict, client: str | None = None) -> bool:
    """Activa impersonación TLS en las opciones. True si quedó activa."""
    target = pick_target(client)
    if target is None:
        return False
    opts["impersonate"] = target
    log.debug("Impersonando %s (cliente %s)", target, client or "por defecto")
    return True


def summary() -> str:
    """Resumen corto para la barra de estado."""
    t = pick_target()
    return str(t) if t else "sin TLS"
