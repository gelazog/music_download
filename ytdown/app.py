"""Ventana principal de ytdown."""
import logging
import threading
import time
import tkinter as tk
from io import BytesIO
from pathlib import Path

import customtkinter as ctk
from PIL import Image
import requests

import yt_dlp
from yt_dlp.utils import sanitize_filename

from . import config as cfg_mod
from . import converter as cvt
from . import deps as deps_mod
from . import updater
from .analyzer import Analyzer
from .constants import (
    ACCENT, ACCENT_HOV, ACCENT2, ACCENT2_HOV,
    BG_DARK, BG_CARD, BG_INPUT, BG_HOVER, BORDER,
    TEXT_PRI, TEXT_SEC, TEXT_MUT,
    GREEN, YELLOW, RED_ERR, ORANGE, BLUE, PURPLE,
    AUDIO_FORMATS, CONTAINERS, LEGACY_FORMATS, MAX_H264_HEIGHT,
    RESOLUTIONS, VIDEO_CODECS, VIDEO_FORMAT,
    pretty_format, video_fmt_key,
)
from .cookies import (BROWSERS, detect_browser, resolve_browser,
                      export_netscape_cookies, cookies_exported,
                      invalidate_caches as cookies_invalidate,
                      cleanup as cookies_cleanup,
                      _is_browser_running, _profile_exists)
from .downloader import DownloadManager, build_ydl_opts
from .history import shared as history
from .info import (fetch as fetch_info, fmt_duration, fmt_size,
                   is_playlist_url, is_pure_playlist_url, split_urls, video_key)
from .jsruntime import summary as js_summary, refresh as js_refresh
from .network import summary as net_summary
from .queue_model import QueueModel, PENDING, OK, ERROR, SKIPPED, CANCELLED
from .widgets import (Banner, ChipBar, FileRow, FormatSelector,
                      ProgressCard, QueueRow, URLBox)

log = logging.getLogger("ytdown.app")

SPEED_LABELS = {"Turbo": "turbo", "Normal": "normal", "Seguro": "seguro"}
SPEED_KEYS = {v: k for k, v in SPEED_LABELS.items()}
SPEED_HELP = {
    "turbo":  "Turbo: sin pausas entre descargas. Lo más rápido, más riesgo de que YouTube te limite (429).",
    "normal": "Normal: pausas cortas entre descargas. Buen equilibrio para playlists.",
    "seguro": "Seguro: pausas largas y un fragmento a la vez. Úsalo si YouTube te está bloqueando.",
}

CONVERT_DEFS = [(f.key, f.label, "◆" if f.lossless else "♫", f.desc)
                for f in cvt.FORMATS]

RES_LABELS   = [lbl for _k, lbl in RESOLUTIONS]
RES_BY_LABEL = {lbl: k for k, lbl in RESOLUTIONS}
RES_TO_LABEL = dict(RESOLUTIONS)

CODEC_LABELS   = [lbl for _k, lbl, _d in VIDEO_CODECS]
CODEC_BY_LABEL = {lbl: k for k, lbl, _d in VIDEO_CODECS}
CODEC_TO_LABEL = {k: lbl for k, lbl, _d in VIDEO_CODECS}

CONT_LABELS   = [lbl for _k, lbl, _d in CONTAINERS]
CONT_BY_LABEL = {lbl: k for k, lbl, _d in CONTAINERS}
CONT_TO_LABEL = {k: lbl for k, lbl, _d in CONTAINERS}

TAB_DESCARGAR = "⬇   Descargar"
TAB_CONVERTIR = "🎛   Convertir"
TAB_HISTORIAL = "🗂   Historial"

# Qué hacer cuando el archivo de destino ya existe.
COLISIONES = {
    "Renombrar":    "renombrar",
    "Omitir":       "omitir",
    "Sobrescribir": "sobrescribir",
}
COLISIONES_INV = {v: k for k, v in COLISIONES.items()}

# Cuántas filas de la cola se pintan de una vez. Tkinter se arrastra con
# cientos de widgets en pantalla; el resto se pinta al pulsar "ver más".
_ROW_PAGE = 60
# Filas nuevas creadas por ciclo, para que desplegar una playlist larga no
# bloquee la ventana mientras se construyen los widgets.
_MAX_NEW_ROWS = 15


class YTDownApp(ctk.CTk):
    VERSION = "v2.3"

    def __init__(self):
        super().__init__()
        self._cfg = cfg_mod.load()

        self.folder         = self._cfg["folder"]

        # Las versiones viejas guardaban "mp4_720" y compañía como formato.
        # Ahora el video es una tarjeta única con su propio panel, así que
        # esas claves se traducen en vez de dejar el selector en blanco.
        fmt_guardado = self._cfg["format"]
        res_guardada = self._cfg.get("video_resolution", "1080")
        if fmt_guardado in LEGACY_FORMATS:
            fmt_guardado, res_guardada = LEGACY_FORMATS[fmt_guardado]
            log.info("Formato antiguo migrado: %s -> video %s",
                     self._cfg["format"], res_guardada)

        self.sel_format     = ctk.StringVar(value=fmt_guardado)
        self.vid_resolution = ctk.StringVar(
            value=RES_TO_LABEL.get(res_guardada, "1080p (Full HD)"))
        self.vid_codec      = ctk.StringVar(value=CODEC_TO_LABEL.get(
            self._cfg.get("video_codec", "compatible"), "Compatible"))
        self.vid_container  = ctk.StringVar(value=CONT_TO_LABEL.get(
            self._cfg.get("video_container", "mp4"), "MP4"))
        self.vid_sound      = ctk.BooleanVar(value=self._cfg.get("video_sound", True))
        self.vid_force      = ctk.BooleanVar(
            value=self._cfg.get("video_force_compat", False))
        self.sel_quality    = ctk.StringVar(value=self._cfg["quality"])
        self.sel_browser    = ctk.StringVar(value=self._cfg["cookies_from_browser"])
        self.sel_speed      = ctk.StringVar(
            value=SPEED_KEYS.get(self._cfg.get("speed", "normal"), "Normal"))
        self.opt_metadata   = ctk.BooleanVar(value=True)
        self.opt_thumbnail  = ctk.BooleanVar(value=False)
        self.opt_subtitles  = ctk.BooleanVar(value=False)
        self.opt_playlist   = ctk.BooleanVar(value=False)
        self.opt_normalize  = ctk.BooleanVar(value=self._cfg.get("normalize_audio", False))
        self.opt_sponsor    = ctk.BooleanVar(value=self._cfg.get("sponsor_block", False))
        self.opt_chapters   = ctk.BooleanVar(value=self._cfg.get("embed_chapters", False))
        self.opt_skip_done  = ctk.BooleanVar(value=self._cfg.get("skip_downloaded", True))
        self.rate_limit_var = ctk.StringVar(value=self._cfg.get("rate_limit", ""))

        self._q            = QueueModel()
        self._dl_manager   = DownloadManager()
        self._rows: list[QueueRow] = []
        self._row_by_id: dict[int, QueueRow] = {}
        self._row_cap      = _ROW_PAGE
        self._render_queued = False
        self._last_prog    = 0.0
        self._active_id: int | None = None
        self._thumb_ref    = None
        self._thumb_cache: dict[str, object] = {}

        # ── Conversor ──────────────────────────────────────────────────────────
        self.cv_format     = ctk.StringVar(value=self._cfg.get("convert_format", "mp3"))
        self.cv_bitrate    = ctk.StringVar(value=self._cfg.get("convert_bitrate",
                                                               cvt.DEFAULT_BITRATE))
        self.cv_normalize  = ctk.BooleanVar(value=self._cfg.get("convert_normalize", False))
        self.cv_metadata   = ctk.BooleanVar(value=self._cfg.get("convert_metadata", True))
        self.cv_cover      = ctk.BooleanVar(value=self._cfg.get("convert_cover", True))
        self.cv_dest_mode  = ctk.StringVar(value=self._cfg.get("convert_dest_mode",
                                                               "misma"))
        self.cv_keep_tree  = ctk.BooleanVar(value=self._cfg.get("convert_keep_tree", True))
        self.cv_collision  = ctk.StringVar(value=COLISIONES_INV.get(
            self._cfg.get("convert_collision", "renombrar"), "Renombrar"))
        self.cv_recursive  = ctk.BooleanVar(value=self._cfg.get("convert_recursive", True))
        self.cv_from_video = ctk.BooleanVar(value=self._cfg.get("convert_from_video", False))
        self.cv_out_folder = self._cfg.get("convert_out_folder", "") or self.folder

        self._files        = cvt.FileList()
        self._converter    = cvt.Converter()
        self._file_rows: list[FileRow] = []
        self._file_row_by_id: dict[int, FileRow] = {}
        self._cv_render_queued = False
        self._cv_active_id: int | None = None
        self._cv_last_prog = 0.0
        self._cv_scan_root: Path | None = None
        self._cv_cap       = _ROW_PAGE

        # ── Historial ──────────────────────────────────────────────────────────
        self._hist_rows: list[ctk.CTkFrame] = []
        self._hist_filter = ctk.StringVar(value="")

        self._analyzer = Analyzer({
            "on_result": self._on_analyzed,
            "on_expand": self._on_playlist_expanded,
            "on_error":  self._on_analyze_error,
            "is_alive":  lambda iid: self._q.by_id(iid) is not None,
        })

        self.title("ytdown — Descargador de YouTube")
        self.geometry("920x820")
        self.minsize(780, 640)
        self.configure(fg_color=BG_DARK)

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        log.info("Navegador detectado: %s", detect_browser() or "ninguno")
        self.after(200, self._update_cookie_hint)
        self.after(400, self._startup_checks)

        # Auto-actualizar yt-dlp en fondo (máx. 1 vez/día) — la defensa #1
        # contra los bloqueos de YouTube es tener el extractor al día.
        updater.update_in_background(self._cfg, cfg_mod.save, self._on_update_status)

    # ======================================================================
    # Construcción de la interfaz
    # ======================================================================
    def _build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        self._build_header()
        self._build_main()
        self._build_statusbar()

    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=56)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.grid_columnconfigure(2, weight=1)
        hdr.grid_propagate(False)

        ctk.CTkLabel(hdr, text="▶", font=("", 18, "bold"),
                     text_color=ACCENT, width=34
                     ).grid(row=0, column=0, padx=(18, 0), pady=14)
        ctk.CTkLabel(hdr, text="ytdown", font=("", 17, "bold"),
                     text_color=TEXT_PRI
                     ).grid(row=0, column=1, sticky="w", padx=2)
        ctk.CTkLabel(hdr, text=self.VERSION, font=("", 11), text_color=TEXT_MUT
                     ).grid(row=0, column=2, sticky="w", padx=8)

        self._hist_btn = ctk.CTkButton(
            hdr, text="🗂 Historial", width=104, height=30, font=("", 11),
            fg_color="transparent", border_width=1, border_color=BORDER,
            text_color=TEXT_SEC, hover_color=BG_INPUT,
            command=self._show_history_tab)
        self._hist_btn.grid(row=0, column=3, padx=6)

        ctk.CTkButton(
            hdr, text="⚙ Requisitos", width=104, height=30, font=("", 11),
            fg_color="transparent", border_width=1, border_color=BORDER,
            text_color=TEXT_SEC, hover_color=BG_INPUT,
            command=self._open_deps_window
        ).grid(row=0, column=4, padx=(0, 18))

    def _build_main(self):
        tabs = ctk.CTkTabview(
            self, fg_color=BG_DARK, corner_radius=0,
            border_width=0, anchor="nw",
            segmented_button_fg_color=BG_CARD,
            segmented_button_selected_color=ACCENT,
            segmented_button_selected_hover_color=ACCENT_HOV,
            segmented_button_unselected_color=BG_CARD,
            segmented_button_unselected_hover_color=BG_INPUT,
            text_color=TEXT_PRI, command=self._on_tab_changed)
        tabs.grid(row=1, column=0, sticky="nsew")
        self._tabs = tabs

        for nombre in (TAB_DESCARGAR, TAB_CONVERTIR, TAB_HISTORIAL):
            tabs.add(nombre)
            hoja = tabs.tab(nombre)
            hoja.grid_columnconfigure(0, weight=1)
            hoja.grid_rowconfigure(0, weight=1)

        self._build_download_tab(tabs.tab(TAB_DESCARGAR))
        self._build_convert_tab(tabs.tab(TAB_CONVERTIR))
        self._build_history_tab(tabs.tab(TAB_HISTORIAL))

    def _on_tab_changed(self):
        """El historial se relee al entrar: puede haber cambiado descargando."""
        if self._tabs.get() == TAB_HISTORIAL:
            self._render_history()

    def _scrollable(self, parent) -> ctk.CTkScrollableFrame:
        scr = ctk.CTkScrollableFrame(parent, fg_color=BG_DARK, corner_radius=0)
        scr.grid(row=0, column=0, sticky="nsew")
        scr.grid_columnconfigure(0, weight=1)
        self._row_i = 0      # las pestañas se construyen una tras otra
        return scr

    def _build_download_tab(self, parent):
        scr = self._scrollable(parent)
        self._scroll = scr

        self._build_url_section(scr)
        self._build_preview_card(scr)
        self._build_format_section(scr)
        self._build_options_section(scr)
        self._build_folder_section(scr)
        self._build_cookies_section(scr)
        self._build_action_buttons(scr)
        self._build_banner(scr)
        self._build_progress(scr)
        self._build_queue_section(scr)

    def _place(self, widget, **kw):
        """Coloca el siguiente bloque en la columna principal."""
        opts = {"padx": 20, "pady": (0, 6)}
        opts.update(kw)
        widget.grid(row=self._row_i, column=0, sticky="ew", **opts)
        self._row_i += 1
        return widget

    def _section_label(self, parent, text, top=12):
        lbl = ctk.CTkLabel(parent, text=text, font=("", 12, "bold"),
                           text_color=TEXT_SEC, anchor="w")
        lbl.grid(row=self._row_i, column=0, sticky="w", padx=20, pady=(top, 5))
        self._row_i += 1
        return lbl

    def _build_url_section(self, p):
        self._section_label(p, "Enlaces  ·  uno por línea", top=18)

        row = ctk.CTkFrame(p, fg_color="transparent")
        row.grid_columnconfigure(0, weight=1)
        self._place(row)

        self._url_box = URLBox(
            row,
            placeholder=("Pega uno o varios enlaces (uno por línea)\n"
                         "https://youtube.com/watch?v=…\n"
                         "https://youtube.com/playlist?list=…"),
            height=82, font=("", 12), fg_color=BG_INPUT,
            border_color=BORDER, border_width=1, wrap="none",
            text_color=TEXT_PRI)
        self._url_box.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        btns = ctk.CTkFrame(row, fg_color="transparent")
        btns.grid(row=0, column=1, sticky="ns")

        ctk.CTkButton(
            btns, text="+ Añadir a la cola", width=148, height=38,
            fg_color=ACCENT, hover_color=ACCENT_HOV,
            font=("", 13, "bold"), command=self._add_to_queue
        ).grid(row=0, column=0, pady=(0, 6))

        ctk.CTkButton(
            btns, text="Vista previa", width=148, height=38,
            fg_color=BG_CARD, hover_color=BG_INPUT, text_color=TEXT_PRI,
            border_width=1, border_color=BORDER,
            font=("", 12), command=self._fetch_info
        ).grid(row=1, column=0)

    def _build_preview_card(self, p):
        self._preview = ctk.CTkFrame(p, fg_color=BG_CARD, corner_radius=10,
                                     border_width=1, border_color=BORDER)
        self._preview.grid_columnconfigure(1, weight=1)
        self._place(self._preview)
        self._preview.grid_remove()

        self._thumb_lbl = ctk.CTkLabel(self._preview, text="", width=160, height=90)
        self._thumb_lbl.grid(row=0, column=0, rowspan=3, padx=14, pady=14)

        self._prev_title = ctk.CTkLabel(self._preview, text="",
                                        font=("", 14, "bold"), text_color=TEXT_PRI,
                                        wraplength=560, justify="left", anchor="w")
        self._prev_title.grid(row=0, column=1, sticky="ew", padx=(0, 14), pady=(14, 2))

        self._prev_channel = ctk.CTkLabel(self._preview, text="",
                                          font=("", 12), text_color=TEXT_SEC, anchor="w")
        self._prev_channel.grid(row=1, column=1, sticky="ew", padx=(0, 14))

        self._prev_meta = ctk.CTkLabel(self._preview, text="",
                                       font=("", 11), text_color=TEXT_MUT, anchor="w")
        self._prev_meta.grid(row=2, column=1, sticky="ew", padx=(0, 14), pady=(2, 14))

    def _build_format_section(self, p):
        self._section_label(p, "Formato de salida")
        self._place(FormatSelector(p, self.sel_format))
        self._build_video_panel(p)

    def _build_video_panel(self, p):
        """Opciones que solo tienen sentido al bajar video."""
        box = ctk.CTkFrame(p, fg_color=BG_CARD, corner_radius=10,
                           border_width=1, border_color=BORDER)
        box.grid_columnconfigure(6, weight=1)
        self._vid_box = box
        self._place(box)

        ctk.CTkLabel(box, text="Resolución:", font=("", 11), text_color=TEXT_SEC
                     ).grid(row=0, column=0, padx=(14, 6), pady=(12, 6))
        ctk.CTkOptionMenu(box, variable=self.vid_resolution, values=RES_LABELS,
                          fg_color=BG_INPUT, button_color="#333",
                          button_hover_color="#444", text_color=TEXT_PRI,
                          font=("", 12), width=150, height=28,
                          command=lambda _v: self._sync_video_hint()
                          ).grid(row=0, column=1, padx=(0, 18), pady=(12, 6))

        ctk.CTkLabel(box, text="Contenedor:", font=("", 11), text_color=TEXT_SEC
                     ).grid(row=0, column=2, padx=(0, 6), pady=(12, 6))
        ctk.CTkOptionMenu(box, variable=self.vid_container, values=CONT_LABELS,
                          fg_color=BG_INPUT, button_color="#333",
                          button_hover_color="#444", text_color=TEXT_PRI,
                          font=("", 12), width=90, height=28,
                          command=lambda _v: self._sync_video_hint()
                          ).grid(row=0, column=3, padx=(0, 18), pady=(12, 6))

        ctk.CTkLabel(box, text="Códec:", font=("", 11), text_color=TEXT_SEC
                     ).grid(row=0, column=4, padx=(0, 6), pady=(12, 6))
        ctk.CTkSegmentedButton(box, values=CODEC_LABELS, variable=self.vid_codec,
                               font=("", 11), height=28, selected_color=ACCENT2,
                               selected_hover_color=ACCENT2_HOV,
                               unselected_color=BG_INPUT,
                               command=lambda _v: self._sync_video_hint()
                               ).grid(row=0, column=5, columnspan=2, sticky="w",
                                      padx=(0, 14), pady=(12, 6))

        chks = ctk.CTkFrame(box, fg_color="transparent")
        chks.grid(row=1, column=0, columnspan=7, sticky="w", padx=14, pady=(0, 6))
        ctk.CTkCheckBox(chks, text="Con sonido", variable=self.vid_sound,
                        font=("", 12), text_color=TEXT_PRI, fg_color=ACCENT,
                        hover_color=ACCENT_HOV, border_color="#444",
                        checkbox_width=18, checkbox_height=18,
                        command=self._sync_video_hint
                        ).grid(row=0, column=0, padx=(0, 22))
        ctk.CTkCheckBox(chks, text="Recodificar si hace falta (lento)",
                        variable=self.vid_force, font=("", 12),
                        text_color=TEXT_PRI, fg_color=ACCENT,
                        hover_color=ACCENT_HOV, border_color="#444",
                        checkbox_width=18, checkbox_height=18,
                        command=self._sync_video_hint
                        ).grid(row=0, column=1)

        self._vid_hint = ctk.CTkLabel(box, text="", font=("", 10),
                                      text_color=TEXT_MUT, anchor="w",
                                      justify="left", wraplength=760)
        self._vid_hint.grid(row=2, column=0, columnspan=7, sticky="ew",
                            padx=14, pady=(0, 12))

        self.sel_format.trace_add("write", lambda *_: self._sync_video_panel())
        self._sync_video_panel()

    def _sync_video_panel(self):
        """El panel solo estorba cuando lo que se baja es audio."""
        if self.sel_format.get() == VIDEO_FORMAT:
            self._vid_box.grid()
            self._sync_video_hint()
        else:
            self._vid_box.grid_remove()

    def _sync_video_hint(self):
        """Explica en cristiano qué va a salir con la combinación elegida.

        Es la parte que faltaba: sin esto uno elige '4K' y se encuentra con un
        archivo que pide códecs y no suena, sin saber por qué.
        """
        res = RES_BY_LABEL.get(self.vid_resolution.get(), "1080")
        codec = CODEC_BY_LABEL.get(self.vid_codec.get(), "compatible")
        cont = CONT_BY_LABEL.get(self.vid_container.get(), "mp4")
        alto = 99999 if res == "best" else int(res)
        sin_h264 = alto > MAX_H264_HEIGHT

        if codec == "compatible" and not sin_h264:
            texto = ("H.264 + AAC en " + cont.upper() +
                     ": se ve y se oye en cualquier reproductor, sin instalar nada.")
            color = GREEN
        elif codec == "compatible" and sin_h264:
            if self.vid_force.get():
                texto = ("YouTube no da H.264 por encima de 1080p. Se bajará VP9/AV1 "
                         "y se recodificará a H.264 + AAC: tarda bastante, pero el "
                         "archivo final se reproduce en cualquier sitio.")
                color = YELLOW
            else:
                texto = ("Ojo: YouTube no ofrece H.264 por encima de 1080p, así que "
                         "a esta resolución bajará VP9/AV1. Marca 'Recodificar' para "
                         "forzar H.264, o baja a 1080p, o usa MKV.")
                color = ORANGE
        elif cont == "mp4":
            texto = ("VP9/AV1 con audio Opus dentro de un MP4: es justo lo que hace "
                     "que Windows pida códecs y que el video se vea sin sonido. "
                     "Usa MKV, o el códec 'Compatible'.")
            color = RED_ERR
        else:
            texto = (f"{cont.upper()} admite VP9/AV1 y Opus sin problema. "
                     "Necesitarás un reproductor moderno (VLC, MPC-HC, Edge).")
            color = YELLOW

        if not self.vid_sound.get():
            texto += "   ·   Sin pista de audio: el archivo saldrá mudo a propósito."

        self._vid_hint.configure(text=texto, text_color=color)

    def _build_options_section(self, p):
        self._section_label(p, "Opciones")

        orow = ctk.CTkFrame(p, fg_color=BG_CARD, corner_radius=10,
                            border_width=1, border_color=BORDER)
        self._place(orow)

        chks = [
            (self.opt_metadata,   "Metadatos"),
            (self.opt_thumbnail,  "Portada"),
            (self.opt_subtitles,  "Subtítulos"),
            (self.opt_playlist,   "Lista completa"),
            (self.opt_normalize,  "Normalizar audio"),
            (self.opt_sponsor,    "Saltar patrocinios"),
            (self.opt_chapters,   "Marcas de capítulo"),
            (self.opt_skip_done,  "No repetir descargados"),
        ]
        for i, (var, txt) in enumerate(chks):
            ctk.CTkCheckBox(
                orow, text=txt, variable=var, font=("", 12),
                text_color=TEXT_PRI, fg_color=ACCENT, checkbox_width=18,
                checkbox_height=18, hover_color=ACCENT_HOV, border_color="#444"
            ).grid(row=i // 4, column=i % 4, padx=(14, 10), pady=(10, 6), sticky="w")

        bottom = ctk.CTkFrame(orow, fg_color="transparent")
        bottom.grid(row=2, column=0, columnspan=4, sticky="ew", padx=14, pady=(2, 10))
        bottom.grid_columnconfigure(2, weight=1)

        ctk.CTkLabel(bottom, text="Calidad audio:", font=("", 11),
                     text_color=TEXT_SEC).grid(row=0, column=0, padx=(0, 6))
        ctk.CTkOptionMenu(
            bottom, variable=self.sel_quality,
            values=["128", "192", "256", "320"],
            fg_color=BG_INPUT, button_color="#333", button_hover_color="#444",
            text_color=TEXT_PRI, font=("", 12), width=78, height=28
        ).grid(row=0, column=1, padx=(0, 18))

        ctk.CTkLabel(bottom, text="Velocidad:", font=("", 11),
                     text_color=TEXT_SEC).grid(row=0, column=3, padx=(0, 6))
        ctk.CTkSegmentedButton(
            bottom, values=list(SPEED_LABELS.keys()), variable=self.sel_speed,
            font=("", 11), height=28, selected_color=ACCENT2,
            selected_hover_color=ACCENT2_HOV, unselected_color=BG_INPUT,
            command=self._on_speed_changed
        ).grid(row=0, column=4)

    def _build_folder_section(self, p):
        frow = ctk.CTkFrame(p, fg_color=BG_CARD, corner_radius=10,
                            border_width=1, border_color=BORDER)
        frow.grid_columnconfigure(1, weight=1)
        self._place(frow)

        ctk.CTkLabel(frow, text="📂", font=("", 14), text_color=TEXT_SEC
                     ).grid(row=0, column=0, padx=(14, 6), pady=10)
        self._folder_lbl = ctk.CTkLabel(frow, text=self.folder, font=("", 11),
                                        text_color=TEXT_SEC, anchor="w")
        self._folder_lbl.grid(row=0, column=1, sticky="ew")

        ctk.CTkButton(frow, text="Cambiar", width=80, height=28,
                      fg_color="transparent", border_width=1, border_color="#444",
                      text_color=TEXT_SEC, hover_color=BG_INPUT, font=("", 11),
                      command=self._pick_folder).grid(row=0, column=2, padx=6)
        ctk.CTkButton(frow, text="Abrir", width=70, height=28,
                      fg_color="transparent", border_width=1, border_color="#444",
                      text_color=TEXT_SEC, hover_color=BG_INPUT, font=("", 11),
                      command=self._open_folder).grid(row=0, column=3, padx=(0, 4))
        ctk.CTkButton(frow, text="Limpiar sobras", width=108, height=28,
                      fg_color="transparent", border_width=1, border_color="#444",
                      text_color=TEXT_SEC, hover_color=BG_INPUT, font=("", 11),
                      command=self._clean_leftovers
                      ).grid(row=0, column=4, padx=(0, 10))

        ctk.CTkLabel(frow, text="Límite:", font=("", 11), text_color=TEXT_SEC
                     ).grid(row=0, column=5, padx=(6, 4))
        ctk.CTkEntry(frow, textvariable=self.rate_limit_var,
                     placeholder_text="ej. 500K", width=80, height=28,
                     font=("", 11), fg_color=BG_INPUT, border_color=BORDER,
                     border_width=1, text_color=TEXT_PRI,
                     placeholder_text_color=TEXT_MUT
                     ).grid(row=0, column=6, padx=(0, 14), pady=8)

    def _build_cookies_section(self, p):
        crow = ctk.CTkFrame(p, fg_color=BG_CARD, corner_radius=10,
                            border_width=1, border_color=BORDER)
        crow.grid_columnconfigure(3, weight=1)
        self._place(crow)

        ctk.CTkLabel(crow, text="🍪", font=("", 14), text_color=TEXT_SEC
                     ).grid(row=0, column=0, padx=(14, 6), pady=10)
        ctk.CTkLabel(crow, text="Cookies:", font=("", 12), text_color=TEXT_SEC
                     ).grid(row=0, column=1, padx=(0, 8))
        ctk.CTkOptionMenu(
            crow, variable=self.sel_browser, values=BROWSERS,
            fg_color=BG_INPUT, button_color="#333", button_hover_color="#444",
            text_color=TEXT_PRI, font=("", 12), width=130, height=28,
            command=self._on_browser_changed,
        ).grid(row=0, column=2, padx=(0, 10), pady=8)

        self._cookie_hint = ctk.CTkLabel(crow, text="", font=("", 10),
                                         text_color=TEXT_MUT, anchor="w")
        self._cookie_hint.grid(row=0, column=3, sticky="w")

        self._btn_export_cookies = ctk.CTkButton(
            crow, text="Exportar cookies", width=130, height=28,
            font=("", 11), fg_color=ACCENT2, hover_color=ACCENT2_HOV,
            command=self._export_cookies)
        self._btn_export_cookies.grid(row=0, column=4, padx=(0, 14), pady=8)

    def _build_action_buttons(self, p):
        brow = ctk.CTkFrame(p, fg_color="transparent")
        brow.grid_columnconfigure(0, weight=1)
        self._place(brow, pady=(6, 6))

        self._btn_download = ctk.CTkButton(
            brow, text="⬇  Descargar todo", height=46,
            font=("", 15, "bold"), fg_color=ACCENT, hover_color=ACCENT_HOV,
            command=self._start_download)
        self._btn_download.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self._btn_pause = ctk.CTkButton(
            brow, text="⏸  Pausar", width=110, height=46,
            font=("", 13), fg_color=ACCENT2, hover_color=ACCENT2_HOV,
            command=self._toggle_pause, state="disabled")
        self._btn_pause.grid(row=0, column=1, padx=(0, 6))

        self._btn_cancel = ctk.CTkButton(
            brow, text="✕ Cancelar", width=100, height=46,
            font=("", 13), fg_color="#333", hover_color="#444",
            text_color=TEXT_PRI, border_width=1, border_color="#555",
            command=self._cancel_download, state="disabled")
        self._btn_cancel.grid(row=0, column=2)

    def _build_banner(self, p):
        self._banner = Banner(p)
        self._place(self._banner)
        self._banner.grid_remove()

    def _build_progress(self, p):
        self._prog_card = ProgressCard(p)
        self._place(self._prog_card)
        self._prog_card.grid_remove()

    def _build_queue_section(self, p):
        head = ctk.CTkFrame(p, fg_color="transparent")
        head.grid_columnconfigure(1, weight=1)
        head.grid(row=self._row_i, column=0, sticky="ew", padx=20, pady=(16, 6))
        self._row_i += 1

        ctk.CTkLabel(head, text="Cola de descargas", font=("", 12, "bold"),
                     text_color=TEXT_SEC).grid(row=0, column=0, sticky="w")

        self._chips = ChipBar(head)
        self._chips.grid(row=0, column=1, sticky="w", padx=12)

        tools = ctk.CTkFrame(head, fg_color="transparent")
        tools.grid(row=0, column=2, sticky="e")
        for i, (txt, cmd) in enumerate([
            ("Marcar todo",     lambda: self._select_all(True)),
            ("Desmarcar",       lambda: self._select_all(False)),
            ("Quitar listos",   self._remove_finished),
            ("Quitar repetidos", self._remove_duplicates),
            ("🗑 Vaciar",        self._clear_queue),
        ]):
            ctk.CTkButton(tools, text=txt, height=26, width=0, font=("", 10),
                          fg_color="transparent", border_width=1,
                          border_color=BORDER, text_color=TEXT_SEC,
                          hover_color=BG_INPUT, command=cmd
                          ).grid(row=0, column=i, padx=(0, 5))

        self._queue_frame = ctk.CTkFrame(p, fg_color=BG_CARD, corner_radius=10,
                                         border_width=1, border_color=BORDER)
        self._queue_frame.grid_columnconfigure(0, weight=1)
        self._place(self._queue_frame, pady=(0, 24))

        self._empty_lbl = ctk.CTkLabel(
            self._queue_frame,
            text="La cola está vacía — pega un enlace arriba para empezar",
            font=("", 12), text_color=TEXT_MUT)

        # Pie fijo: botón de descarga de la cola y, si hace falta, "ver más filas".
        self._more_rows_btn = ctk.CTkButton(
            self._queue_frame, text="", height=30, font=("", 11),
            fg_color="transparent", border_width=1, border_color=BORDER,
            text_color=BLUE, hover_color=BG_INPUT,
            command=self._show_more_rows)

        self._queue_btn = ctk.CTkButton(
            self._queue_frame, text="▶  Descargar cola", height=38,
            font=("", 13, "bold"), fg_color=ACCENT, hover_color=ACCENT_HOV,
            command=self._start_queue)

        self._render_queue()

    def _build_statusbar(self):
        bar = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=0, height=30)
        bar.grid(row=2, column=0, sticky="ew")
        bar.grid_propagate(False)
        bar.grid_columnconfigure(0, weight=1)

        self._status_lbl = ctk.CTkLabel(bar, text="Listo", font=("", 11),
                                        text_color=TEXT_MUT, anchor="w")
        self._status_lbl.grid(row=0, column=0, padx=14, sticky="w")

        self._env_lbl = ctk.CTkLabel(
            bar,
            text=(f"yt-dlp {yt_dlp.version.__version__} · JS: {js_summary()}"
                  f" · TLS: {net_summary()}"),
            font=("", 10), text_color=TEXT_MUT)
        self._env_lbl.grid(row=0, column=1, padx=14)

    # ======================================================================
    # Arranque
    # ======================================================================
    def _startup_checks(self):
        """Depura el historial y avisa si falta algo importante."""
        def worker():
            gone = history.prune_missing()
            faltan = deps_mod.missing()
            self._ui(lambda: self._after_startup_checks(gone, faltan))

        threading.Thread(target=worker, daemon=True, name="ytdown-startup").start()

    def _after_startup_checks(self, pruned: int, faltan: list):
        if pruned:
            log.info("Historial depurado: %d archivos ya no existían", pruned)

        criticas = [d for d in faltan if d.required]
        if criticas:
            nombres = ", ".join(d.label for d in criticas)
            self._banner.show(
                f"Falta {nombres}. Sin eso la app no puede convertir ni unir "
                f"los archivos.", "error",
                action="Instalar", on_action=self._open_deps_window)
        elif faltan:
            nombres = ", ".join(d.label for d in faltan)
            self._banner.show(
                f"Opcional sin instalar: {nombres}. Con eso hay más formatos "
                f"disponibles y menos bloqueos de YouTube.", "warn",
                action="Ver", on_action=self._open_deps_window)
        elif len(history):
            self._set_status(
                f"Listo  ·  {len(history)} descargas en el historial", TEXT_MUT)

    # ======================================================================
    # Ayudantes varios
    # ======================================================================
    def _on_update_status(self, msg: str, ok):
        if msg:
            self._ui(lambda: self._set_status(msg, GREEN if ok else TEXT_SEC))

    def _on_speed_changed(self, label: str):
        key = SPEED_LABELS.get(label, "normal")
        self._set_status(SPEED_HELP[key], BLUE)

    def _speed(self) -> str:
        return SPEED_LABELS.get(self.sel_speed.get(), "normal")

    def _update_cookie_hint(self):
        self._on_browser_changed(self.sel_browser.get())

    def _on_browser_changed(self, value: str):
        resolved = resolve_browser(value)

        if value == "ninguno":
            hint, color = "sin cookies — funciona para la mayoría de videos", TEXT_SEC
        elif resolved == "ninguno":
            hint, color = "auto → ningún navegador detectado", RED_ERR
        elif not _profile_exists(resolved):
            hint, color = f"{resolved} — no instalado", RED_ERR
        elif cookies_exported():
            hint, color = f"{resolved} — cookies exportadas ✓", GREEN
        elif _is_browser_running(resolved):
            hint = f"{resolved} abierto — ciérralo y presiona 'Exportar cookies'"
            color = ORANGE
        else:
            hint = f"{resolved} detectado — presiona 'Exportar cookies'"
            color = YELLOW

        self._cookie_hint.configure(text=hint, text_color=color)

    def _export_cookies(self):
        browser = self.sel_browser.get()
        resolved = resolve_browser(browser)
        if resolved == "ninguno":
            self._banner.show("Elige primero un navegador en la lista de cookies.",
                              "warn")
            return

        if _is_browser_running(resolved):
            self._banner.show(
                f"{resolved.capitalize()} está abierto. Ciérralo del todo "
                f"(incluido el icono de la bandeja) y vuelve a intentarlo.", "warn")
            self._on_browser_changed(browser)
            return

        self._btn_export_cookies.configure(state="disabled", text="Exportando…")
        self._set_status(f"Exportando cookies de {resolved}…", YELLOW)

        def worker():
            path, err = export_netscape_cookies(resolved)
            cookies_invalidate()

            def done():
                self._btn_export_cookies.configure(state="normal",
                                                   text="Exportar cookies")
                self._on_browser_changed(browser)
                if err:
                    self._banner.show(f"No se pudieron leer las cookies: {err[:120]}",
                                      "error")
                else:
                    self._banner.show(
                        f"Cookies de {resolved} listas. Ahora puedes bajar videos "
                        f"con restricción de edad o privados de tu cuenta.", "ok")
            self._ui(done)

        threading.Thread(target=worker, daemon=True).start()

    def _ui(self, fn, *args):
        """Ejecuta algo en el hilo de la interfaz desde un hilo de fondo.

        Si la ventana ya se cerró, tkinter revienta con RuntimeError/TclError.
        No es un error real: simplemente ya no hay nada que actualizar.
        """
        try:
            self.after(0, fn, *args)
        except (RuntimeError, tk.TclError):
            log.debug("Actualización de UI descartada: la ventana ya no existe")

    def _set_status(self, msg: str, color: str = TEXT_MUT):
        self._status_lbl.configure(text=msg, text_color=color)

    def _pick_folder(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(initialdir=self.folder)
        if d:
            self.folder = d
            self._folder_lbl.configure(text=d)
            log.info("Carpeta cambiada: %s", d)

    def _open_folder(self):
        import subprocess
        Path(self.folder).mkdir(parents=True, exist_ok=True)
        subprocess.Popen(["explorer", str(Path(self.folder))])

    def _clean_leftovers(self):
        """Borra restos de descargas fallidas, siempre preguntando antes."""
        from tkinter import messagebox
        # La raíz de un disco no: recorrerla entera es lento y ahí hay .webp de
        # cachés de navegador y de medio programa instalado. No es basura mía.
        raiz = Path(self.folder).resolve()
        if raiz.parent == raiz:
            self._banner.show(
                f"Tu carpeta de descargas es {raiz}, la raíz del disco. No voy "
                f"a rastrear un disco entero buscando qué borrar: elige una "
                f"carpeta concreta con 'Cambiar'.", "warn")
            return

        sobras, total = cvt.find_leftovers(self.folder)
        if not sobras:
            self._banner.show(
                "No hay sobras en la carpeta de descargas: todo limpio.", "ok")
            return

        ejemplos = "\n".join(f"  · {p.name[:58]}" for p in sobras[:5])
        if len(sobras) > 5:
            ejemplos += f"\n  … y {len(sobras) - 5} más"
        if not messagebox.askyesno(
                "Limpiar sobras",
                f"Encontré {len(sobras)} archivo(s) ({fmt_size(total)}) que "
                f"dejaron descargas a medias:\n\n{ejemplos}\n\n"
                "Son portadas sueltas (.webp) y descargas incompletas (.part). "
                "No se toca ningún audio ni video.\n\n¿Los borro?",
                icon="warning", parent=self):
            return

        borrados = fallos = 0
        for p in sobras:
            try:
                p.unlink()
                borrados += 1
            except OSError as e:
                log.warning("No se pudo borrar %s: %s", p, e)
                fallos += 1

        msg = f"{borrados} archivo(s) borrados ({fmt_size(total)} liberados)."
        if fallos:
            msg += f"  {fallos} no se pudieron borrar (¿en uso?)."
        self._banner.show(msg, "ok" if not fallos else "warn")
        self._set_status(msg, GREEN)

    def _short(self, url: str) -> str:
        return url if len(url) <= 60 else url[:57] + "…"

    def _video_choice(self) -> tuple[str, str, str, bool]:
        """(resolución, códec, contenedor, con_sonido) tal como están en la UI."""
        return (RES_BY_LABEL.get(self.vid_resolution.get(), "1080"),
                CODEC_BY_LABEL.get(self.vid_codec.get(), "compatible"),
                CONT_BY_LABEL.get(self.vid_container.get(), "mp4"),
                bool(self.vid_sound.get()))

    def _current_fmt_key(self) -> str:
        """Clave de formato del item, la que también usa el historial.

        Para video lleva resolución y contenedor dentro: el mismo video en
        720p y en 1080p no son la misma descarga.
        """
        fmt = self.sel_format.get()
        if fmt != VIDEO_FORMAT:
            return fmt
        res, _codec, cont, sonido = self._video_choice()
        return video_fmt_key(res, cont, sonido)

    def _build_opts(self, url: str, *, playlist: bool = False,
                    subfolder: str = "") -> dict:
        folder = str(Path(self.folder) / subfolder) if subfolder else self.folder
        res, codec, cont, sonido = self._video_choice()
        return build_ydl_opts(
            url=url,
            folder=folder,
            fmt=self.sel_format.get(),
            quality=self.sel_quality.get(),
            resolution=res,
            vcodec=codec,
            container=cont,
            with_audio=sonido,
            force_compat=bool(self.vid_force.get()),
            playlist=playlist,
            metadata=self.opt_metadata.get(),
            thumbnail=self.opt_thumbnail.get(),
            subtitles=self.opt_subtitles.get(),
            normalize=self.opt_normalize.get(),
            sponsor_block=self.opt_sponsor.get(),
            embed_chapters=self.opt_chapters.get(),
            rate_limit=self.rate_limit_var.get().strip(),
            browser=self.sel_browser.get(),
            speed=self._speed(),
        )

    # ======================================================================
    # Vista previa
    # ======================================================================
    def _fetch_info(self):
        urls = split_urls(self._url_box.get_text())
        if not urls:
            self._banner.show("Pega al menos un enlace en el cuadro de arriba.", "warn")
            return
        url = urls[0]
        if len(urls) > 1:
            self._set_status(f"Vista previa del primero de {len(urls)} enlaces…", YELLOW)
        else:
            self._set_status("Consultando YouTube…", YELLOW)
        self._preview.grid_remove()
        threading.Thread(target=self._fetch_info_worker, args=(url,),
                         daemon=True).start()

    def _fetch_info_worker(self, url: str):
        try:
            info     = fetch_info(url, self.sel_browser.get())
            is_plist = info.get("_type") == "playlist"
            title    = info.get("title", "Sin título")
            channel  = info.get("channel") or info.get("uploader") or ""
            count    = info.get("playlist_count") or 1
            dur      = info.get("duration") or 0
            thumb    = (info.get("thumbnail")
                        or (info.get("thumbnails") or [{}])[-1].get("url", ""))
            meta     = f"{count} videos" if is_plist else fmt_duration(dur)

            # Ya descargado? Decirlo aquí ahorra un viaje a la cola.
            extra = ""
            if not is_plist:
                fmts = history.formats_of(video_key(url))
                if fmts:
                    etiquetas = ", ".join(pretty_format(f) for f in fmts)
                    extra = f"   ·   ya lo descargaste en {etiquetas}"

            self._ui(lambda: self._show_preview(title, channel,
                                                     meta + extra, thumb))
            self._ui(lambda: self._set_status("Información obtenida ✓", GREEN))
        except Exception as e:
            from .downloader import friendly_error
            msg = friendly_error(str(e))
            log.error("Error al obtener info: %s", e)
            self._ui(lambda m=msg: self._banner.show(
                f"No se pudo leer el enlace: {m}", "error"))
            self._ui(lambda: self._set_status("No se pudo leer el enlace", RED_ERR))

    def _show_preview(self, title, channel, meta, thumb_url):
        self._prev_title.configure(text=title)
        self._prev_channel.configure(text=channel)
        self._prev_meta.configure(text=meta)
        self._preview.grid()
        if thumb_url:
            threading.Thread(target=self._load_thumb, args=(thumb_url,),
                             daemon=True).start()

    def _load_thumb(self, url: str):
        img = self._thumb_cache.get(url)
        if img is None:
            try:
                resp = requests.get(url, timeout=6)
                pil = Image.open(BytesIO(resp.content)).resize((160, 90))
                img = ctk.CTkImage(light_image=pil, dark_image=pil, size=(160, 90))
            except Exception as e:
                log.debug("Miniatura no disponible: %s", e)
                return
            if len(self._thumb_cache) > 40:
                self._thumb_cache.clear()
            self._thumb_cache[url] = img
        self._thumb_ref = img
        self._ui(lambda: self._thumb_lbl.configure(image=img))

    # ======================================================================
    # Cola: alta de elementos
    # ======================================================================
    def _wants_full_playlist(self, url: str) -> bool:
        """Una URL de playlist pura siempre se expande; una de video con
        ``&list=`` solo si el usuario marcó 'Lista completa'."""
        if is_pure_playlist_url(url):
            return True
        return is_playlist_url(url) and self.opt_playlist.get()

    def _add_to_queue(self) -> int:
        """Añade a la cola todos los enlaces del cuadro. Devuelve cuántos añadió."""
        urls = split_urls(self._url_box.get_text())
        if not urls:
            self._banner.show("Pega al menos un enlace en el cuadro de arriba.", "warn")
            return 0

        fmt = self._current_fmt_key()
        browser = self.sel_browser.get()
        skip_done = self.opt_skip_done.get()

        nuevos = grupos = repetidos_cola = ya_descargados = 0
        group_keys = {n["key"] for n in self._q.nodes if n["kind"] == "group"}

        for url in urls:
            if self._wants_full_playlist(url):
                key = video_key(url)
                if key in group_keys:
                    repetidos_cola += 1
                    continue
                group = self._q.add_group(url, f"Playlist  ·  {self._short(url)}", fmt)
                group_keys.add(key)
                self._analyzer.submit(group["id"], url, browser=browser,
                                      expand_playlist=True)
                grupos += 1
                continue

            if self._q.contains(url, fmt):
                repetidos_cola += 1
                continue

            item = self._q.make_item(url, self._short(url), fmt,
                                     self._build_opts(url, playlist=False))
            self._q.add_item(item)
            if skip_done and self._q.mark_duplicates([item]):
                ya_descargados += 1
            self._analyzer.submit(item["id"], url, browser=browser)
            nuevos += 1

        self._url_box.clear()
        self._preview.grid_remove()
        self._render_queue()
        self._report_added(nuevos, grupos, repetidos_cola, ya_descargados,
                           len(urls))
        return nuevos + grupos

    def _report_added(self, nuevos, grupos, repetidos_cola, ya_descargados, total):
        partes = []
        if nuevos:
            partes.append(f"{nuevos} video{'s' if nuevos != 1 else ''}")
        if grupos:
            partes.append(f"{grupos} playlist{'s' if grupos != 1 else ''}"
                          f" (leyendo su contenido…)")

        if not partes:
            if repetidos_cola:
                self._banner.show(
                    f"{'Ese enlace ya estaba' if total == 1 else 'Esos enlaces ya estaban'}"
                    f" en la cola. No se añadió nada.", "warn")
            else:
                self._banner.show("No se reconoció ningún enlace válido.", "warn")
            return

        msg = "Añadido a la cola: " + " y ".join(partes) + "."
        avisos = []
        if repetidos_cola:
            avisos.append(f"{repetidos_cola} ya estaban en la cola")
        if ya_descargados:
            avisos.append(f"{ya_descargados} ya los tenías descargados "
                          f"(quedan desmarcados)")
        if avisos:
            msg += "  Omitidos: " + " y ".join(avisos) + "."

        self._banner.show(msg, "ok" if not avisos else "info")
        self._set_status(msg, GREEN)

    # ======================================================================
    # Cola: callbacks del analizador
    # ======================================================================
    def _on_analyzed(self, item_id: int, data: dict):
        def apply():
            item = self._q.by_id(item_id)
            if not item or item["kind"] != "item":
                return
            item["title"]    = data["title"]
            item["analyzed"] = True
            item["duration"] = data.get("duration") or 0
            if not item["duplicate"]:
                bits = [b for b in (data.get("channel"),
                                    fmt_duration(data.get("duration"))) if b]
                if data.get("count"):
                    bits.append(f"{data['count']} videos")
                item["detail"] = "  ·  ".join(bits)
            self._render_queue()
        self._ui(apply)

    def _on_playlist_expanded(self, group_id: int, entries: list[dict], title: str):
        """Convierte el grupo de playlist en una lista de videos elegibles."""
        def apply():
            group = self._q.by_id(group_id)
            if not group or group["kind"] != "group":
                return

            group["title"] = title
            if not entries:
                group["analyzed"] = True
                group["error"] = ("no se pudo leer — puede ser privada, "
                                  "estar vacía o requerir cookies")
                self._render_queue()
                self._banner.show(
                    f"La playlist '{title[:40]}' no devolvió ningún video. "
                    f"Si es privada, exporta las cookies de tu navegador.", "warn")
                return

            # Las opciones son idénticas para todos los videos de la playlist:
            # construirlas una sola vez ahorra cientos de dicts en listas largas.
            sub = sanitize_filename(title, restricted=False)
            shared_opts = self._build_opts(group["url"], playlist=False,
                                           subfolder=sub)

            vistos = {it["key"] for it in self._q.items()}
            hijos, repetidos = [], 0
            for e in entries:
                key = video_key(e["url"])
                if key in vistos:
                    repetidos += 1
                    continue
                vistos.add(key)
                hijos.append(self._q.make_item(
                    e["url"], e["title"], group["fmt"], shared_opts,
                    detail=fmt_duration(e.get("duration")),
                    duration=e.get("duration") or 0,
                    analyzed=True, parent=group_id))

            self._q.fill_group(group, hijos)
            ya = (self._q.mark_duplicates(hijos)
                  if self.opt_skip_done.get() else 0)

            self._render_queue()
            self._announce_playlist(title, len(hijos), ya, repetidos)

        self._ui(apply)

    def _announce_playlist(self, title: str, total: int, ya: int, repetidos: int):
        nuevos = total - ya
        msg = f"Playlist '{title[:40]}': {total} videos."
        if ya:
            msg += (f"  {ya} ya los tenías (desmarcados) y {nuevos} son nuevos.")
        if repetidos:
            msg += f"  {repetidos} ya estaban en la cola."
        msg += "  Usa la flecha ▸ para ver la lista y elegir cuáles bajar."
        self._banner.show(msg, "info")
        self._set_status(f"Playlist expandida: {total} videos, {nuevos} por descargar",
                         GREEN)

    def _on_analyze_error(self, item_id: int, msg: str):
        """No se pudieron leer los metadatos. No es fatal: al descargar se
        vuelve a intentar, y ahí sí se rota de cliente."""
        from .downloader import friendly_error
        amable = friendly_error(msg)

        def apply():
            node = self._q.by_id(item_id)
            if not node:
                return
            node["analyzed"] = True
            if node["kind"] == "group":
                node["error"] = amable
                self._banner.show(
                    f"No se pudo leer la playlist: {amable}", "error")
            else:
                node["detail"] = "sin datos — se intentará igual al descargar"
            self._render_queue()

        self._ui(apply)

    # ======================================================================
    # Cola: interacción
    # ======================================================================
    def _toggle_group(self, group_id: int):
        self._q.toggle_expanded(group_id)
        self._render_queue()

    def _toggle_select(self, node_id: int):
        node = self._q.by_id(node_id)
        if not node:
            return
        if node["kind"] == "group":
            sel, total = self._q.group_selection(node)
            self._q.set_selected(node_id, sel < total)
        else:
            self._q.set_selected(node_id, not node["selected"])
        self._render_queue()

    def _remove_node(self, node_id: int):
        node = self._q.by_id(node_id)
        etiqueta = node["title"][:50] if node else ""
        ok, motivo = self._q.remove(node_id)
        if not ok:
            self._banner.show(f"No se puede quitar: {motivo}.", "warn")
            return
        self._set_status(f"Quitado de la cola: {etiqueta}", TEXT_SEC)
        self._render_queue()

    def _show_more_children(self, group_id: int):
        self._q.show_more(group_id)
        self._render_queue()

    def _show_more_rows(self):
        self._row_cap += _ROW_PAGE
        self._render_queue()

    def _select_all(self, value: bool):
        self._q.select_all(value)
        self._render_queue()
        self._set_status("Todo marcado" if value else "Todo desmarcado", TEXT_SEC)

    def _remove_finished(self):
        n = self._q.remove_where(lambda it: it["status"] in (OK, SKIPPED))
        self._render_queue()
        self._set_status(f"{n} elemento(s) completado(s) quitados de la cola"
                         if n else "No hay nada completado que quitar", TEXT_SEC)

    def _remove_duplicates(self):
        n = self._q.remove_where(lambda it: it["duplicate"])
        self._render_queue()
        self._banner.show(
            f"{n} video(s) que ya tenías descargados salieron de la cola."
            if n else "No hay repetidos en la cola.", "ok" if n else "info")

    def _clear_queue(self):
        if self._dl_manager.running:
            self._banner.show("Cancela la descarga en curso antes de vaciar la cola.",
                              "warn")
            return
        self._analyzer.drop_pending()
        self._q.clear()
        self._row_cap = _ROW_PAGE
        self._render_queue()
        self._set_status("Cola vaciada", TEXT_SEC)

    # ======================================================================
    # Cola: pintado incremental
    # ======================================================================
    def _render_queue(self):
        """Agrupa repintados: una ráfaga de callbacks produce un solo dibujo."""
        if self._render_queued:
            return
        self._render_queued = True
        self.after(50, self._do_render_queue)

    def _do_render_queue(self):
        self._render_queued = False
        rows = self._q.visible_rows()

        if not rows:
            for w in self._rows:
                w.hide()
            self._row_by_id.clear()
            self._more_rows_btn.grid_remove()
            self._queue_btn.grid_remove()
            self._empty_lbl.grid(row=0, column=0, pady=24)
            self._update_chips()
            return

        self._empty_lbl.grid_remove()

        visibles = rows[: self._row_cap]
        callbacks = {
            "toggle": self._toggle_group,
            "select": self._toggle_select,
            "remove": self._remove_node,
            "more":   self._show_more_children,
        }

        # Crear una fila cuesta unos milisegundos, así que desplegar una
        # playlist entera de golpe congelaba la ventana un par de segundos.
        # Se crean por tandas: lo primero aparece ya y el resto va entrando.
        faltan = len(visibles) - len(self._rows)
        streaming = faltan > _MAX_NEW_ROWS
        for _ in range(min(faltan, _MAX_NEW_ROWS)):
            self._rows.append(QueueRow(self._queue_frame, callbacks))
        if streaming:
            visibles = visibles[: len(self._rows)]
            self._render_queued = True
            self.after(16, self._do_render_queue)

        self._row_by_id.clear()
        for i, row in enumerate(visibles):
            widget = self._rows[i]
            widget.update_row(row, i, self._q)
            widget.place_at(i)
            if row["kind"] == "item":
                self._row_by_id[row["node"]["id"]] = widget

        for widget in self._rows[len(visibles):]:
            widget.hide()

        siguiente = len(visibles)
        # Mientras se crean filas por tandas, el número de "ocultas" aún está
        # bajando: mostrarlo haría parpadear una cifra que no significa nada.
        ocultas = 0 if streaming else len(rows) - len(visibles)
        if ocultas > 0:
            self._more_rows_btn.configure(text=f"▾  Ver {ocultas} filas más")
            self._more_rows_btn.grid(row=siguiente, column=0, sticky="ew",
                                     padx=8, pady=(4, 0))
            siguiente += 1
        else:
            self._more_rows_btn.grid_remove()

        self._update_queue_button(siguiente)
        self._update_chips()

    def _update_queue_button(self, row: int):
        pendientes = len(self._q.downloadable())
        if self._dl_manager.running:
            self._queue_btn.configure(text="Descargando…", state="disabled")
        elif pendientes:
            plural = "s" if pendientes != 1 else ""
            self._queue_btn.configure(
                text=f"▶  Descargar {pendientes} seleccionado{plural}",
                state="normal")
        else:
            self._queue_btn.configure(text="Nada seleccionado para descargar",
                                      state="disabled")
        self._queue_btn.grid(row=row, column=0, sticky="ew", padx=8, pady=(6, 8))

    def _update_chips(self):
        c = self._q.counts()
        chips = [("total", f"{c['total']} en cola", TEXT_SEC)]
        if c["groups"]:
            chips.append(("grupos", f"{c['groups']} playlist(s)", ACCENT2))
        if c["pending"]:
            chips.append(("pend", f"{c['pending']} por bajar", YELLOW))
        if c["ok"]:
            chips.append(("ok", f"{c['ok']} listos", GREEN))
        if c["skipped"]:
            chips.append(("skip", f"{c['skipped']} omitidos", PURPLE))
        if c["duplicate"]:
            chips.append(("dup", f"{c['duplicate']} repetidos", PURPLE))
        if c["error"]:
            chips.append(("err", f"{c['error']} con error", RED_ERR))
        self._chips.render(chips)

    # ======================================================================
    # Descarga
    # ======================================================================
    def _start_download(self):
        """Añade lo que haya en el cuadro y descarga toda la cola seleccionada."""
        if self._dl_manager.running:
            return
        if self._url_box.get_text():
            self._add_to_queue()
        if self._q.is_empty():
            self._banner.show("La cola está vacía. Pega un enlace para empezar.",
                              "warn")
            return
        # Si hay playlists resolviéndose, esperar a que terminen: si no, se
        # lanzaría la descarga con media lista sin cargar.
        if self._analyzer.busy:
            self._set_status("Esperando a que terminen de leerse las playlists…",
                             YELLOW)
            self.after(500, self._start_download)
            return
        self._start_queue()

    def _start_queue(self):
        if self._dl_manager.running:
            return

        seleccionados = self._q.downloadable()
        if not seleccionados:
            c = self._q.counts()
            if c["duplicate"]:
                self._banner.show(
                    "Todo lo que queda en la cola ya lo descargaste antes. "
                    "Márcalo a mano si quieres bajarlo otra vez, o desactiva "
                    "'No repetir descargados'.", "info")
            else:
                self._banner.show("No hay nada marcado para descargar.", "warn")
            return

        skip_done = self.opt_skip_done.get()
        for item in seleccionados:
            item["status"] = PENDING
            item["error"] = ""
            item["progress"] = 0.0

        # skip_known solo protege de repetir algo *dentro de esta misma tanda*
        # (la misma canción en dos playlists). Si el usuario marcó a mano algo
        # que ya estaba descargado, es porque lo quiere otra vez: se respeta.
        jobs = [{
            "id":         it["id"],
            "url":        it["url"],
            "opts":       it["opts"],
            "key":        it["key"],
            "fmt":        it["fmt"],
            "title":      it["title"],
            "skip_known": skip_done and not it["duplicate"],
        } for it in seleccionados]

        log.info("Iniciando cola: %d items, modo %s", len(jobs), self._speed())
        self._banner.hide()
        self._render_queue()
        self._launch_jobs(jobs)

    def _launch_jobs(self, jobs: list[dict]):
        self._prog_card.grid()
        self._prog_card.update(pct=0.0, title="Preparando…", info="")
        self._prog_card.set_color(ACCENT)
        self._btn_download.configure(state="disabled", text="Descargando…")
        self._btn_pause.configure(state="normal", text="⏸  Pausar", fg_color=ACCENT2)
        self._btn_cancel.configure(state="normal")
        self._dl_manager.start(jobs, {
            "on_progress":     self._on_progress,
            "on_item_start":   self._on_item_start,
            "on_item_done":    self._on_item_done,
            "on_all_done":     self._on_all_done,
            "on_client_retry": self._on_client_retry,
            "on_rate_limit":   self._on_rate_limit,
        }, speed=self._speed())

    def _toggle_pause(self):
        if not self._dl_manager.running:
            return
        if self._dl_manager.paused:
            self._dl_manager.resume()
            self._btn_pause.configure(text="⏸  Pausar", fg_color=ACCENT2)
            self._prog_card.set_color(ACCENT)
            self._set_status("Descarga reanudada", GREEN)
        else:
            self._dl_manager.pause()
            self._btn_pause.configure(text="▶  Reanudar", fg_color=GREEN)
            self._prog_card.set_color(ORANGE)
            self._set_status("En pausa — pulsa Reanudar para continuar", ORANGE)

    def _cancel_download(self):
        if not self._dl_manager.running:
            return
        self._dl_manager.cancel()
        self._set_status("Cancelando… (se detendrá al terminar el fragmento actual)",
                         ORANGE)

    # -- callbacks del worker (llegan desde su hilo) ----------------------------
    def _on_progress(self, d: dict):
        estado = d.get("status")
        if estado == "downloading":
            # Los hooks de yt-dlp llegan a decenas por segundo: sin este freno,
            # la UI se pasa el rato repintando en vez de descargando.
            now = time.monotonic()
            if now - self._last_prog < 0.12:
                return
            self._last_prog = now

            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done  = d.get("downloaded_bytes") or 0
            pct   = (done / total) if total else 0.0
            speed = d.get("_speed_str", "").strip() or "—"
            eta   = d.get("_eta_str", "").strip() or "—"
            tamano = f"  ·  {fmt_size(done)} de {fmt_size(total)}" if total else ""
            info  = f"{speed}  ·  faltan {eta}{tamano}"
            nombre = Path(d.get("filename", "")).name
            self._ui(self._apply_progress, pct, nombre, info)
        elif estado == "finished":
            self._ui(lambda: self._prog_card.update(
                pct=1.0, info="Convirtiendo con FFmpeg…"))

    def _apply_progress(self, pct: float, nombre: str, info: str):
        self._prog_card.update(title=nombre, info=info, pct=pct)
        if self._active_id is not None:
            item = self._q.by_id(self._active_id)
            if item:
                item["progress"] = pct
            # Solo se toca la barra de esa fila: repintar la cola entera en
            # cada tick es justamente lo que hacía que la ventana se trabara.
            if fila := self._row_by_id.get(self._active_id):
                fila.set_progress(pct)

    def _set_item(self, item_id: int, **campos):
        def apply():
            item = self._q.by_id(item_id)
            if item:
                item.update(campos)
                self._render_queue()
        self._ui(apply)

    def _on_client_retry(self, item_id: int, client: str):
        self._set_item(item_id, status="retrying")
        self._ui(lambda c=client: self._set_status(
            f"YouTube rechazó la descarga — probando con el cliente '{c}'…", ORANGE))
        self._ui(lambda c=client: self._prog_card.update(
            info=f"Reintentando con {c}…"))

    def _on_rate_limit(self, item_id: int, secs: int):
        self._set_item(item_id, status="waiting")
        mensaje = (f"YouTube limitó tu IP (error 429). Esperando {secs}s antes de "
                   f"reintentar. Si se repite, cambia la velocidad a 'Seguro'.")
        self._ui(lambda: self._banner.show(mensaje, "warn"))
        self._ui(lambda: self._prog_card.update(info=f"Esperando {secs}s…"))

    def _on_item_start(self, item_id: int, pos: int, total: int):
        self._active_id = item_id
        self._set_item(item_id, status="downloading", progress=0.0)
        item = self._q.by_id(item_id)
        titulo = item["title"] if item else ""
        self._ui(lambda t=titulo: self._set_status(
            f"[{pos + 1}/{total}]  {t[:60]}", YELLOW))

    def _on_item_done(self, item_id: int, ok: bool, err: str, info: dict):
        info = info or {}
        if info.get("skipped"):
            self._set_item(item_id, status=SKIPPED, progress=0.0,
                           detail="ya estaba descargado — no se repitió")
            return
        if err == "cancelado":
            self._set_item(item_id, status=CANCELLED, progress=0.0)
            return
        if ok:
            destino = Path(info.get("path", "")).name
            self._set_item(item_id, status=OK, error="", progress=1.0,
                           detail=destino or "guardado")
        else:
            self._set_item(item_id, status=ERROR, error=err, progress=0.0)
            log.error("Item %s falló: %s", item_id, err)

    def _on_all_done(self, resumen: dict):
        self._ui(lambda: self._finish_run(resumen))

    def _finish_run(self, r: dict):
        self._active_id = None
        history.save(force=True)   # el guardado va diferido: al acabar, a disco
        self._btn_download.configure(state="normal", text="⬇  Descargar todo")
        self._btn_pause.configure(state="disabled", text="⏸  Pausar",
                                  fg_color=ACCENT2)
        self._btn_cancel.configure(state="disabled")

        # Los que quedaron a medias vuelven a estar pendientes; los completados
        # se conservan visibles para que se vea qué se bajó.
        for item in self._q.items():
            if item["status"] in ("downloading", "retrying", "waiting"):
                item["status"] = PENDING
                item["progress"] = 0.0
        self._render_queue()

        partes = []
        if r.get("ok"):
            partes.append(f"{r['ok']} descargado(s)")
        if r.get("skipped"):
            partes.append(f"{r['skipped']} omitido(s) por estar repetidos")
        if r.get("error"):
            partes.append(f"{r['error']} con error")
        detalle = ", ".join(partes) if partes else "sin cambios"
        minutos = int(r.get("elapsed", 0) // 60)
        segundos = int(r.get("elapsed", 0) % 60)
        tiempo = f"{minutos} min {segundos} s" if minutos else f"{segundos} s"

        if r.get("cancelled"):
            self._prog_card.update(info="Cancelado")
            self._banner.show(f"Descarga cancelada. {detalle}.", "warn")
            self._set_status("Cancelado", ORANGE)
            return

        self._prog_card.update(pct=1.0, info=f"Terminado en {tiempo}")
        tono = "error" if r.get("error") and not r.get("ok") else "ok"
        self._banner.show(
            f"Listo en {tiempo}: {detalle}.  Archivos en {self.folder}", tono,
            action="Abrir carpeta", on_action=self._open_folder)
        self._set_status(f"✓ {detalle}  ·  {tiempo}", GREEN)

    # ======================================================================
    # Ventanas auxiliares
    # ======================================================================
    def _modal(self, title: str, size: str) -> ctk.CTkToplevel:
        win = ctk.CTkToplevel(self)
        win.title(title)
        win.geometry(size)
        win.configure(fg_color=BG_DARK)
        win.transient(self)
        win.after(120, win.lift)
        win.after(140, win.grab_set)
        return win

    def _open_deps_window(self):
        win = self._modal("Requisitos de ytdown", "640x520")
        win.grid_columnconfigure(0, weight=1)
        win.grid_rowconfigure(1, weight=1)

        ctk.CTkLabel(win, text="Qué necesita la app y qué tienes instalado",
                     font=("", 14, "bold"), text_color=TEXT_PRI
                     ).grid(row=0, column=0, sticky="w", padx=18, pady=(16, 8))

        cont = ctk.CTkScrollableFrame(win, fg_color=BG_DARK)
        cont.grid(row=1, column=0, sticky="nsew", padx=12)
        cont.grid_columnconfigure(0, weight=1)

        estado_lbl = ctk.CTkLabel(win, text="", font=("", 11),
                                  text_color=TEXT_SEC, anchor="w")
        estado_lbl.grid(row=2, column=0, sticky="ew", padx=18, pady=(6, 14))

        def repintar():
            for w in cont.winfo_children():
                w.destroy()
            for i, estado in enumerate(deps_mod.check_all()):
                self._dep_card(cont, i, estado, estado_lbl, repintar)

        def on_progress(msg, ok):
            self._ui(lambda: estado_lbl.configure(
                text=msg, text_color=GREEN if ok else
                (RED_ERR if ok is False else YELLOW)))

        def on_done(_resultados):
            js_refresh()
            self._ui(repintar)
            self._ui(lambda: self._env_lbl.configure(
                text=(f"yt-dlp {yt_dlp.version.__version__} · JS: {js_summary()}"
                      f" · TLS: {net_summary()}")))

        self._dep_install = lambda dep: deps_mod.install_many([dep], on_progress, on_done)
        repintar()

    def _dep_card(self, parent, i: int, estado: dict, estado_lbl, repintar):
        dep, ok = estado["dep"], estado["ok"]
        card = ctk.CTkFrame(parent, fg_color=BG_CARD, corner_radius=8,
                            border_width=1, border_color=BORDER)
        card.grid(row=i, column=0, sticky="ew", pady=4, padx=4)
        card.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(card, text="✓" if ok else ("✕" if dep.required else "○"),
                     font=("", 15), width=26,
                     text_color=GREEN if ok else (RED_ERR if dep.required else ORANGE)
                     ).grid(row=0, column=0, rowspan=2, padx=(10, 4), pady=8)

        etiqueta = dep.label + ("" if dep.required else "   (opcional)")
        ctk.CTkLabel(card, text=etiqueta, font=("", 12, "bold"),
                     text_color=TEXT_PRI, anchor="w"
                     ).grid(row=0, column=1, sticky="ew", pady=(8, 0))

        detalle = dep.why if not ok else f"{dep.why}  ·  {estado['where'][:60]}"
        ctk.CTkLabel(card, text=detalle, font=("", 10), text_color=TEXT_MUT,
                     anchor="w", wraplength=380, justify="left"
                     ).grid(row=1, column=1, sticky="ew", pady=(0, 8))

        if ok:
            return

        if deps_mod.can_autoinstall(dep):
            ctk.CTkButton(card, text="Instalar", width=96, height=30,
                          font=("", 11, "bold"), fg_color=ACCENT,
                          hover_color=ACCENT_HOV,
                          command=lambda d=dep: self._dep_install(d)
                          ).grid(row=0, column=2, rowspan=2, padx=10)
        else:
            estado_lbl.configure(text=dep.manual, text_color=ORANGE)
            ctk.CTkLabel(card, text="manual", font=("", 10), text_color=ORANGE,
                         width=96).grid(row=0, column=2, rowspan=2, padx=10)

    # ======================================================================
    # Pestaña: Convertir
    # ======================================================================
    def _build_convert_tab(self, parent):
        scr = self._scrollable(parent)

        self._section_label(scr, "Qué quieres convertir", top=18)
        self._build_convert_source(scr)
        self._build_convert_list(scr)
        self._section_label(scr, "Convertir a")
        self._place(FormatSelector(scr, self.cv_format, defs=CONVERT_DEFS))
        self._build_convert_options(scr)
        self._section_label(scr, "Dónde guardar el resultado")
        self._build_convert_dest(scr)
        self._build_convert_actions(scr)

        self.cv_format.trace_add("write", lambda *_: self._sync_bitrate_state())
        self.cv_dest_mode.trace_add("write", lambda *_: self._sync_dest_state())
        self._sync_bitrate_state()
        self._sync_dest_state()

    def _build_convert_source(self, p):
        box = ctk.CTkFrame(p, fg_color=BG_CARD, corner_radius=10,
                           border_width=1, border_color=BORDER)
        box.grid_columnconfigure(2, weight=1)
        self._place(box)

        ctk.CTkButton(box, text="📄  Elegir archivos…", width=168, height=34,
                      font=("", 12, "bold"), fg_color=ACCENT2,
                      hover_color=ACCENT2_HOV, command=self._pick_files
                      ).grid(row=0, column=0, padx=(14, 6), pady=(12, 4))
        ctk.CTkButton(box, text="📂  Elegir carpeta…", width=168, height=34,
                      font=("", 12, "bold"), fg_color=BG_INPUT,
                      hover_color=BG_HOVER, text_color=TEXT_PRI,
                      border_width=1, border_color=BORDER,
                      command=self._pick_convert_folder
                      ).grid(row=0, column=1, padx=6, pady=(12, 4))

        self._cv_source_lbl = ctk.CTkLabel(
            box, text="Elige archivos sueltos o una carpeta entera.",
            font=("", 11), text_color=TEXT_MUT, anchor="w")
        self._cv_source_lbl.grid(row=0, column=2, sticky="ew", padx=10)

        opts = ctk.CTkFrame(box, fg_color="transparent")
        opts.grid(row=1, column=0, columnspan=3, sticky="w", padx=14, pady=(0, 10))
        for i, (var, txt) in enumerate([
                (self.cv_recursive,  "Mirar también dentro de las subcarpetas"),
                (self.cv_from_video, "Sacar el audio de los videos que encuentre")]):
            ctk.CTkCheckBox(opts, text=txt, variable=var, font=("", 11),
                            text_color=TEXT_SEC, fg_color=ACCENT2,
                            hover_color=ACCENT2_HOV, border_color="#444",
                            checkbox_width=17, checkbox_height=17
                            ).grid(row=0, column=i, padx=(0, 20), sticky="w")

    def _build_convert_list(self, p):
        head = ctk.CTkFrame(p, fg_color="transparent")
        head.grid_columnconfigure(1, weight=1)
        head.grid(row=self._row_i, column=0, sticky="ew", padx=20, pady=(12, 5))
        self._row_i += 1

        ctk.CTkLabel(head, text="Archivos", font=("", 12, "bold"),
                     text_color=TEXT_SEC).grid(row=0, column=0, sticky="w")
        self._cv_chips = ChipBar(head)
        self._cv_chips.grid(row=0, column=1, sticky="w", padx=12)

        tools = ctk.CTkFrame(head, fg_color="transparent")
        tools.grid(row=0, column=2, sticky="e")
        for i, (txt, cmd) in enumerate([
                ("Marcar todo",   lambda: self._select_all_files(True)),
                ("Desmarcar",     lambda: self._select_all_files(False)),
                ("Quitar listos", self._remove_converted),
                ("🗑 Vaciar",      self._clear_files)]):
            ctk.CTkButton(tools, text=txt, height=26, width=0, font=("", 10),
                          fg_color="transparent", border_width=1,
                          border_color=BORDER, text_color=TEXT_SEC,
                          hover_color=BG_INPUT, command=cmd
                          ).grid(row=0, column=i, padx=(0, 5))

        self._cv_list = ctk.CTkFrame(p, fg_color=BG_CARD, corner_radius=10,
                                     border_width=1, border_color=BORDER)
        self._cv_list.grid_columnconfigure(0, weight=1)
        self._place(self._cv_list)

        self._cv_empty = ctk.CTkLabel(
            self._cv_list,
            text="Aún no hay archivos — usa los botones de arriba",
            font=("", 12), text_color=TEXT_MUT)
        self._cv_more_btn = ctk.CTkButton(
            self._cv_list, text="", height=30, font=("", 11),
            fg_color="transparent", border_width=1, border_color=BORDER,
            text_color=BLUE, hover_color=BG_INPUT,
            command=self._show_more_files)
        self._render_files()

    def _build_convert_options(self, p):
        box = ctk.CTkFrame(p, fg_color=BG_CARD, corner_radius=10,
                           border_width=1, border_color=BORDER)
        box.grid_columnconfigure(6, weight=1)
        self._place(box)

        ctk.CTkLabel(box, text="Calidad:", font=("", 11), text_color=TEXT_SEC
                     ).grid(row=0, column=0, padx=(14, 6), pady=10)
        self._cv_bitrate_menu = ctk.CTkOptionMenu(
            box, variable=self.cv_bitrate, values=cvt.BITRATES,
            fg_color=BG_INPUT, button_color="#333", button_hover_color="#444",
            text_color=TEXT_PRI, font=("", 12), width=90, height=28)
        self._cv_bitrate_menu.grid(row=0, column=1, padx=(0, 4))
        self._cv_bitrate_hint = ctk.CTkLabel(box, text="kbps", font=("", 10),
                                             text_color=TEXT_MUT)
        self._cv_bitrate_hint.grid(row=0, column=2, padx=(0, 18))

        for i, (var, txt) in enumerate([
                (self.cv_normalize, "Igualar el volumen"),
                (self.cv_metadata,  "Conservar título y artista"),
                (self.cv_cover,     "Conservar la portada")]):
            ctk.CTkCheckBox(box, text=txt, variable=var, font=("", 11),
                            text_color=TEXT_PRI, fg_color=ACCENT,
                            hover_color=ACCENT_HOV, border_color="#444",
                            checkbox_width=17, checkbox_height=17
                            ).grid(row=0, column=3 + i, padx=(0, 16), pady=10,
                                   sticky="w")

    def _build_convert_dest(self, p):
        box = ctk.CTkFrame(p, fg_color=BG_CARD, corner_radius=10,
                           border_width=1, border_color=BORDER)
        box.grid_columnconfigure(1, weight=1)
        self._place(box)

        ctk.CTkRadioButton(box, text="Junto a cada original",
                           variable=self.cv_dest_mode, value="misma",
                           font=("", 12), text_color=TEXT_PRI,
                           fg_color=ACCENT, hover_color=ACCENT_HOV,
                           radiobutton_width=17, radiobutton_height=17
                           ).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 4))

        ctk.CTkRadioButton(box, text="En esta carpeta:",
                           variable=self.cv_dest_mode, value="carpeta",
                           font=("", 12), text_color=TEXT_PRI,
                           fg_color=ACCENT, hover_color=ACCENT_HOV,
                           radiobutton_width=17, radiobutton_height=17
                           ).grid(row=1, column=0, sticky="w", padx=14, pady=(0, 10))

        self._cv_folder_lbl = ctk.CTkLabel(box, text=self.cv_out_folder,
                                           font=("", 11), text_color=TEXT_SEC,
                                           anchor="w")
        self._cv_folder_lbl.grid(row=1, column=1, sticky="ew", padx=(6, 6))

        self._cv_btn_folder = ctk.CTkButton(
            box, text="Cambiar", width=80, height=28, fg_color="transparent",
            border_width=1, border_color="#444", text_color=TEXT_SEC,
            hover_color=BG_INPUT, font=("", 11), command=self._pick_out_folder)
        self._cv_btn_folder.grid(row=1, column=2, padx=4)

        self._cv_btn_open = ctk.CTkButton(
            box, text="Abrir", width=70, height=28, fg_color="transparent",
            border_width=1, border_color="#444", text_color=TEXT_SEC,
            hover_color=BG_INPUT, font=("", 11),
            command=lambda: self._open_path(self.cv_out_folder))
        self._cv_btn_open.grid(row=1, column=3, padx=(0, 10))

        extra = ctk.CTkFrame(box, fg_color="transparent")
        extra.grid(row=2, column=0, columnspan=4, sticky="ew", padx=14, pady=(0, 10))

        self._cv_tree_chk = ctk.CTkCheckBox(
            extra, text="Repetir la estructura de subcarpetas",
            variable=self.cv_keep_tree, font=("", 11), text_color=TEXT_SEC,
            fg_color=ACCENT2, hover_color=ACCENT2_HOV, border_color="#444",
            checkbox_width=17, checkbox_height=17)
        self._cv_tree_chk.grid(row=0, column=0, sticky="w", padx=(0, 24))

        ctk.CTkLabel(extra, text="Si ya existe el archivo:", font=("", 11),
                     text_color=TEXT_SEC).grid(row=0, column=1, padx=(0, 6))
        ctk.CTkOptionMenu(extra, variable=self.cv_collision,
                          values=list(COLISIONES.keys()),
                          fg_color=BG_INPUT, button_color="#333",
                          button_hover_color="#444", text_color=TEXT_PRI,
                          font=("", 11), width=124, height=28
                          ).grid(row=0, column=2)

    def _build_convert_actions(self, p):
        brow = ctk.CTkFrame(p, fg_color="transparent")
        brow.grid_columnconfigure(0, weight=1)
        self._place(brow, pady=(8, 6))

        self._cv_btn_convert = ctk.CTkButton(
            brow, text="🎛  Convertir", height=46, font=("", 15, "bold"),
            fg_color=ACCENT, hover_color=ACCENT_HOV, command=self._start_convert)
        self._cv_btn_convert.grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self._cv_btn_pause = ctk.CTkButton(
            brow, text="⏸  Pausar", width=110, height=46, font=("", 13),
            fg_color=ACCENT2, hover_color=ACCENT2_HOV,
            command=self._toggle_convert_pause, state="disabled")
        self._cv_btn_pause.grid(row=0, column=1, padx=(0, 6))

        self._cv_btn_cancel = ctk.CTkButton(
            brow, text="✕ Cancelar", width=100, height=46, font=("", 13),
            fg_color="#333", hover_color="#444", text_color=TEXT_PRI,
            border_width=1, border_color="#555",
            command=self._cancel_convert, state="disabled")
        self._cv_btn_cancel.grid(row=0, column=2)

        self._cv_banner = Banner(p)
        self._place(self._cv_banner)
        self._cv_banner.grid_remove()

        self._cv_prog = ProgressCard(p)
        self._place(self._cv_prog, pady=(0, 24))
        self._cv_prog.grid_remove()

    # -- estado dependiente de las opciones ------------------------------------
    def _sync_bitrate_state(self):
        """En FLAC y WAV no hay bitrate que elegir: se apaga el selector."""
        fmt = cvt.FORMATS_BY_KEY.get(self.cv_format.get())
        sin_perdida = bool(fmt and fmt.lossless)
        self._cv_bitrate_menu.configure(state="disabled" if sin_perdida else "normal")
        self._cv_bitrate_hint.configure(
            text="no aplica (sin pérdida)" if sin_perdida else "kbps")

    def _sync_dest_state(self):
        misma = self.cv_dest_mode.get() == "misma"
        estado = "disabled" if misma else "normal"
        for w in (self._cv_btn_folder, self._cv_btn_open, self._cv_tree_chk):
            w.configure(state=estado)
        self._cv_folder_lbl.configure(
            text_color=TEXT_MUT if misma else TEXT_SEC)

    # -- origen ----------------------------------------------------------------
    def _pick_files(self):
        from tkinter import filedialog
        audio = tuple(sorted(f"*{e}" for e in cvt.AUDIO_EXTS))
        todo = tuple(sorted(f"*{e}" for e in (cvt.AUDIO_EXTS | cvt.VIDEO_EXTS)))
        # Varios grupos y los patrones como tupla (la forma documentada de Tk).
        # "Todos los archivos" va siempre disponible: si alguna extensión rara
        # se nos escapa, el usuario no se queda atascado.
        rutas = filedialog.askopenfilenames(
            title="Elige los archivos a convertir",
            initialdir=self.folder,
            filetypes=[("Audio y video", todo),
                       ("Solo audio", audio),
                       ("Todos los archivos", "*.*")])
        if not rutas:
            return
        # Archivos sueltos: no hay raíz común fiable para replicar carpetas.
        self._cv_scan_root = None
        nuevos, repetidos = self._files.add(rutas)
        self._cv_source_lbl.configure(
            text=f"{len(self._files)} archivo(s) elegidos a mano",
            text_color=TEXT_SEC)
        self._render_files()
        # Los archivos elegidos a mano entran tal cual: si el usuario los
        # señaló, los quiere, aunque la extensión no nos suene.
        self._report_scan(cvt.ScanResult(files=list(rutas)), nuevos, repetidos)

    def _pick_convert_folder(self):
        from tkinter import filedialog
        carpeta = filedialog.askdirectory(
            title="Elige la carpeta con los archivos a convertir",
            initialdir=self.folder)
        if not carpeta:
            return
        self._cv_source_lbl.configure(text="Buscando archivos…", text_color=YELLOW)
        recursivo = self.cv_recursive.get()
        con_video = self.cv_from_video.get()

        # Una carpeta con muchos archivos (o en red) tarda: fuera del hilo de la UI.
        def worker():
            aviso = lambda txt: self._ui(
                lambda: self._cv_source_lbl.configure(text=txt, text_color=YELLOW))
            resultado = cvt.scan_folder(carpeta, recursive=recursivo,
                                        include_video=con_video,
                                        on_progress=aviso)
            self._ui(self._folder_scanned, carpeta, resultado)

        threading.Thread(target=worker, daemon=True, name="ytdown-scan").start()

    def _folder_scanned(self, carpeta: str, res):
        self._cv_scan_root = Path(carpeta)
        nuevos, repetidos = self._files.add(res.files)
        nombre = Path(carpeta).name or carpeta
        self._cv_source_lbl.configure(
            text=f"{nombre}  ·  {len(self._files)} archivo(s) en la lista",
            text_color=TEXT_SEC)
        self._render_files()
        self._report_scan(res, nuevos, repetidos)

    def _report_scan(self, res, nuevos: int, repetidos: int):
        """Dice qué entró y, sobre todo, qué se quedó fuera y por qué.

        Sin esto, una carpeta con 80 MP3 y 45 archivos sueltos parecía que
        "solo detectaba MP3", cuando lo que pasaba es que no había más audio.
        """
        descartes = []
        if res.videos:
            descartes.append(
                f"{res.videos} con video (marca 'Sacar el audio de los videos')")
        if res.skipped_others:
            detalle = ", ".join(f"{n} {ext}" for ext, n in res.top_others())
            resto = res.skipped_others - sum(n for _e, n in res.top_others())
            if resto > 0:
                detalle += f" y {resto} más"
            descartes.append(f"{res.skipped_others} que no son audio ({detalle})")

        if not res.files:
            aviso = "Ahí no encontré ningún archivo de audio."
            if descartes:
                aviso += "  Descarté: " + "; ".join(descartes) + "."
            else:
                aviso += "  La carpeta está vacía o solo tiene subcarpetas."
            self._cv_banner.show(aviso, "warn")
            return

        if nuevos:
            msg = f"{nuevos} archivo(s) de audio añadidos."
            if repetidos:
                msg += f"  {repetidos} ya estaban en la lista."
        else:
            msg = f"Los {repetidos} archivo(s) ya estaban en la lista."

        if descartes:
            msg += "  No entraron: " + "; ".join(descartes) + "."
        if res.probed:
            msg += f"  ({res.probed} comprobados por dentro para ver si eran "
            msg += "solo audio.)"
        self._cv_banner.show(msg, "ok" if nuevos else "info")

    # -- interacción con la lista ----------------------------------------------
    def _toggle_file(self, item_id: int):
        self._files.toggle(item_id)
        self._render_files()

    def _remove_file(self, item_id: int):
        if not self._files.remove(item_id):
            self._cv_banner.show("Ese archivo se está convirtiendo ahora mismo.",
                                 "warn")
            return
        self._render_files()

    def _select_all_files(self, value: bool):
        self._files.select_all(value)
        self._render_files()

    def _remove_converted(self):
        n = self._files.remove_where(lambda it: it["status"] in ("ok", "skipped"))
        self._render_files()
        self._set_status(f"{n} archivo(s) ya convertidos fuera de la lista"
                         if n else "No hay nada convertido que quitar", TEXT_SEC)

    def _clear_files(self):
        if self._converter.running:
            self._cv_banner.show("Cancela la conversión antes de vaciar la lista.",
                                 "warn")
            return
        self._files.clear()
        self._cv_cap = _ROW_PAGE
        self._cv_scan_root = None
        self._cv_source_lbl.configure(
            text="Elige archivos sueltos o una carpeta entera.", text_color=TEXT_MUT)
        self._render_files()

    def _show_more_files(self):
        self._cv_cap += _ROW_PAGE
        self._render_files()

    def _pick_out_folder(self):
        from tkinter import filedialog
        d = filedialog.askdirectory(initialdir=self.cv_out_folder or self.folder)
        if d:
            self.cv_out_folder = d
            self._cv_folder_lbl.configure(text=d)

    def _open_path(self, ruta: str):
        import subprocess
        destino = Path(ruta)
        if not destino.exists():
            self._cv_banner.show(f"Esa carpeta ya no existe: {ruta}", "warn")
            return
        subprocess.Popen(["explorer", str(destino)])

    # -- pintado de la lista ---------------------------------------------------
    def _render_files(self):
        if self._cv_render_queued:
            return
        self._cv_render_queued = True
        self.after(50, self._do_render_files)

    def _do_render_files(self):
        self._cv_render_queued = False
        items = self._files.items

        if not items:
            for w in self._file_rows:
                w.hide()
            self._file_row_by_id.clear()
            self._cv_more_btn.grid_remove()
            self._cv_empty.grid(row=0, column=0, pady=24)
            self._update_cv_chips()
            self._update_convert_button()
            return

        self._cv_empty.grid_remove()
        visibles = items[: self._cv_cap]
        callbacks = {"select": self._toggle_file, "remove": self._remove_file}

        faltan = len(visibles) - len(self._file_rows)
        streaming = faltan > _MAX_NEW_ROWS
        for _ in range(min(faltan, _MAX_NEW_ROWS)):
            self._file_rows.append(FileRow(self._cv_list, callbacks))
        if streaming:
            visibles = visibles[: len(self._file_rows)]
            self._cv_render_queued = True
            self.after(16, self._do_render_files)

        self._file_row_by_id.clear()
        for i, item in enumerate(visibles):
            widget = self._file_rows[i]
            widget.update_row(item, i)
            widget.place_at(i)
            self._file_row_by_id[item["id"]] = widget

        for widget in self._file_rows[len(visibles):]:
            widget.hide()

        ocultas = 0 if streaming else len(items) - len(visibles)
        if ocultas > 0:
            self._cv_more_btn.configure(text=f"▾  Ver {ocultas} archivos más")
            self._cv_more_btn.grid(row=len(visibles), column=0, sticky="ew",
                                   padx=8, pady=(4, 8))
        else:
            self._cv_more_btn.grid_remove()

        self._update_cv_chips()
        self._update_convert_button()

    def _update_cv_chips(self):
        c = self._files.counts()
        chips = [("total", f"{c['total']} archivos", TEXT_SEC)]
        if c["bytes"]:
            chips.append(("peso", fmt_size(c["bytes"]), TEXT_MUT))
        if c["selected"]:
            chips.append(("sel", f"{c['selected']} marcados", YELLOW))
        if c["ok"]:
            chips.append(("ok", f"{c['ok']} convertidos", GREEN))
        if c["skipped"]:
            chips.append(("skip", f"{c['skipped']} omitidos", PURPLE))
        if c["error"]:
            chips.append(("err", f"{c['error']} con error", RED_ERR))
        self._cv_chips.render(chips)

    def _update_convert_button(self):
        pendientes = len(self._files.selected())
        fmt = cvt.FORMATS_BY_KEY.get(self.cv_format.get())
        etiqueta = fmt.label if fmt else ""
        if self._converter.running:
            self._cv_btn_convert.configure(text="Convirtiendo…", state="disabled")
        elif pendientes:
            plural = "s" if pendientes != 1 else ""
            self._cv_btn_convert.configure(
                text=f"🎛  Convertir {pendientes} archivo{plural} a {etiqueta}",
                state="normal")
        else:
            self._cv_btn_convert.configure(text="🎛  Convertir", state="disabled")

    # -- ejecución -------------------------------------------------------------
    def _start_convert(self):
        if self._converter.running:
            return
        if not cvt.ffmpeg_available():
            self._cv_banner.show(
                "Falta FFmpeg, que es justo lo que hace la conversión.", "error",
                action="Instalar", on_action=self._open_deps_window)
            return

        seleccion = self._files.selected()
        if not seleccion:
            self._cv_banner.show("No hay ningún archivo marcado para convertir.",
                                 "warn")
            return

        fmt = cvt.FORMATS_BY_KEY[self.cv_format.get()]
        a_carpeta = self.cv_dest_mode.get() == "carpeta"
        out_dir = Path(self.cv_out_folder) if a_carpeta else None
        raiz = self._cv_scan_root if (a_carpeta and self.cv_keep_tree.get()) else None
        politica = COLISIONES.get(self.cv_collision.get(), "renombrar")

        jobs, omitidos = [], 0
        for item in seleccion:
            src = Path(item["src"])
            destino = cvt.target_path(src, fmt, out_dir, keep_tree_from=raiz)
            final = cvt.resolve_collision(destino, politica)
            if final is None:
                item["status"] = "skipped"
                item["detail"] = "ya existe convertido — no se tocó"
                omitidos += 1
                continue
            item["status"] = "pending"
            item["error"] = ""
            item["progress"] = 0.0
            item["dst"] = str(final)
            jobs.append({
                "id":            item["id"],
                "src":           item["src"],
                "dst":           str(final),
                "fmt":           fmt.key,
                "bitrate":       self.cv_bitrate.get(),
                "normalize":     self.cv_normalize.get(),
                "keep_cover":    self.cv_cover.get(),
                "keep_metadata": self.cv_metadata.get(),
                "duration":      item.get("duration") or 0,
            })

        if not jobs:
            self._render_files()
            self._cv_banner.show(
                f"Los {omitidos} archivos ya existen convertidos, así que no se "
                f"hizo nada. Cambia 'Si ya existe el archivo' a Renombrar o "
                f"Sobrescribir si quieres rehacerlos.", "info")
            return

        aviso = f"Convirtiendo {len(jobs)} archivo(s) a {fmt.label}"
        if omitidos:
            aviso += f" ({omitidos} ya existían y se dejaron como estaban)"
        self._set_status(aviso, YELLOW)

        self._cv_banner.hide()
        self._cv_prog.grid()
        self._cv_prog.update(pct=0.0, title="Preparando…", info="")
        self._cv_prog.set_color(ACCENT2)
        self._cv_btn_convert.configure(state="disabled", text="Convirtiendo…")
        self._cv_btn_pause.configure(state="normal", text="⏸  Pausar",
                                     fg_color=ACCENT2)
        self._cv_btn_cancel.configure(state="normal")
        self._render_files()

        self._converter.start(jobs, {
            "on_item_start": self._on_convert_start,
            "on_progress":   self._on_convert_progress,
            "on_item_done":  self._on_convert_done,
            "on_all_done":   self._on_convert_all_done,
        })

    def _toggle_convert_pause(self):
        if not self._converter.running:
            return
        if self._converter.paused:
            self._converter.resume()
            self._cv_btn_pause.configure(text="⏸  Pausar", fg_color=ACCENT2)
            self._cv_prog.set_color(ACCENT2)
            self._set_status("Conversión reanudada", GREEN)
        else:
            self._converter.pause()
            self._cv_btn_pause.configure(text="▶  Reanudar", fg_color=GREEN)
            self._cv_prog.set_color(ORANGE)
            self._set_status("Conversión en pausa", ORANGE)

    def _cancel_convert(self):
        if self._converter.running:
            self._converter.cancel()
            self._set_status("Cancelando la conversión…", ORANGE)

    def _set_file(self, item_id: int, **campos):
        def apply():
            item = self._files.by_id(item_id)
            if item:
                item.update(campos)
                self._render_files()
        self._ui(apply)

    def _on_convert_start(self, item_id: int, pos: int, total: int, nombre: str):
        self._cv_active_id = item_id
        self._set_file(item_id, status="converting", progress=0.0, detail="")
        self._ui(lambda: self._set_status(
            f"[{pos + 1}/{total}] Convirtiendo {nombre[:55]}…", YELLOW))

    def _on_convert_progress(self, item_id: int, pct: float, segundos: float):
        now = time.monotonic()
        if pct < 1.0 and now - self._cv_last_prog < 0.12:
            return
        self._cv_last_prog = now
        self._ui(self._apply_convert_progress, item_id, pct, segundos)

    def _apply_convert_progress(self, item_id: int, pct: float, segundos: float):
        item = self._files.by_id(item_id)
        if item:
            item["progress"] = pct
            self._cv_prog.update(title=item["name"], pct=pct,
                                 info=f"{fmt_duration(segundos)} procesados")
        if fila := self._file_row_by_id.get(item_id):
            fila.set_progress(pct)

    def _on_convert_done(self, item_id: int, ok: bool, mensaje: str, destino: str):
        if mensaje == "cancelado":
            self._set_file(item_id, status="pending", progress=0.0)
            return
        if mensaje == "omitido":
            self._set_file(item_id, status="skipped", progress=0.0,
                           detail="el destino era el propio archivo")
            return
        if ok:
            self._set_file(item_id, status="ok", error="", progress=1.0,
                           detail=f"→ {Path(destino).name}", dst=destino)
        else:
            self._set_file(item_id, status="error", error=mensaje, progress=0.0)

    def _on_convert_all_done(self, resumen: dict):
        self._ui(self._finish_convert, resumen)

    def _finish_convert(self, r: dict):
        self._cv_active_id = None
        self._cv_btn_convert.configure(state="normal")
        self._cv_btn_pause.configure(state="disabled", text="⏸  Pausar",
                                     fg_color=ACCENT2)
        self._cv_btn_cancel.configure(state="disabled")
        self._render_files()

        partes = []
        if r.get("ok"):
            partes.append(f"{r['ok']} convertido(s)")
        if r.get("omitidos"):
            partes.append(f"{r['omitidos']} omitido(s)")
        if r.get("error"):
            partes.append(f"{r['error']} con error")
        detalle = ", ".join(partes) if partes else "sin cambios"
        segundos = int(r.get("elapsed", 0))
        tiempo = (f"{segundos // 60} min {segundos % 60} s" if segundos >= 60
                  else f"{segundos} s")

        if r.get("cancelado"):
            self._cv_prog.update(info="Cancelado")
            self._cv_banner.show(f"Conversión cancelada. {detalle}.", "warn")
            self._set_status("Conversión cancelada", ORANGE)
            return

        peso = fmt_size(r.get("bytes", 0))
        self._cv_prog.update(pct=1.0, info=f"Terminado en {tiempo}")
        destino = (self.cv_out_folder if self.cv_dest_mode.get() == "carpeta"
                   else "la carpeta de cada original")
        tono = "error" if r.get("error") and not r.get("ok") else "ok"
        self._cv_banner.show(
            f"Listo en {tiempo}: {detalle}" + (f" ({peso})" if peso else "")
            + f".  Los archivos están en {destino}.", tono,
            action="Abrir carpeta" if self.cv_dest_mode.get() == "carpeta" else "",
            on_action=(lambda: self._open_path(self.cv_out_folder))
            if self.cv_dest_mode.get() == "carpeta" else None)
        self._set_status(f"✓ {detalle}  ·  {tiempo}", GREEN)

    # ======================================================================
    # Pestaña: Historial
    # ======================================================================
    def _show_history_tab(self):
        self._tabs.set(TAB_HISTORIAL)
        self._render_history()

    def _build_history_tab(self, parent):
        parent.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(16, 8))
        head.grid_columnconfigure(2, weight=1)

        self._hist_title = ctk.CTkLabel(head, text="", font=("", 13, "bold"),
                                        text_color=TEXT_PRI, anchor="w")
        self._hist_title.grid(row=0, column=0, sticky="w", padx=(0, 14))

        buscador = ctk.CTkEntry(
            head, textvariable=self._hist_filter, width=230, height=30,
            placeholder_text="Buscar por título o enlace…", font=("", 11),
            fg_color=BG_INPUT, border_color=BORDER, border_width=1,
            text_color=TEXT_PRI, placeholder_text_color=TEXT_MUT)
        buscador.grid(row=0, column=1, sticky="w")
        self._hist_filter.trace_add("write", lambda *_: self._render_history())

        tools = ctk.CTkFrame(head, fg_color="transparent")
        tools.grid(row=0, column=3, sticky="e")
        for i, (txt, cmd) in enumerate([
                ("📋 Copiar enlaces", self._copy_all_links),
                ("Exportar .txt",     lambda: self._export_history("txt")),
                ("Exportar .csv",     lambda: self._export_history("csv")),
                ("Depurar",           self._prune_history)]):
            ctk.CTkButton(tools, text=txt, height=28, width=0, font=("", 10),
                          fg_color="transparent", border_width=1,
                          border_color=BORDER, text_color=TEXT_SEC,
                          hover_color=BG_INPUT, command=cmd
                          ).grid(row=0, column=i, padx=(0, 5))
        ctk.CTkButton(tools, text="🗑 Vaciar", height=28, width=0, font=("", 10),
                      fg_color=RED_ERR, hover_color="#B91C1C",
                      command=self._clear_history
                      ).grid(row=0, column=4)

        self._hist_scroll = ctk.CTkScrollableFrame(parent, fg_color=BG_DARK,
                                                   corner_radius=0)
        self._hist_scroll.grid(row=1, column=0, sticky="nsew", padx=14)
        self._hist_scroll.grid_columnconfigure(0, weight=1)

        self._hist_note = ctk.CTkLabel(
            parent,
            text=("Lo que está aquí no se vuelve a descargar mientras "
                  "'No repetir descargados' esté activo en la pestaña Descargar."),
            font=("", 10), text_color=TEXT_MUT, anchor="w")
        self._hist_note.grid(row=2, column=0, sticky="ew", padx=20, pady=(8, 12))

        self._render_history()

    def _filtered_history(self) -> list[dict]:
        entradas = history.entries()
        texto = self._hist_filter.get().strip().lower()
        if not texto:
            return entradas
        return [e for e in entradas
                if texto in (e.get("title") or "").lower()
                or texto in (e.get("url") or "").lower()]

    def _render_history(self):
        entradas = self._filtered_history()
        total = len(history)

        if not entradas:
            self._hist_title.configure(
                text="El historial está vacío" if not total
                else "Nada coincide con esa búsqueda")
        else:
            etiqueta = f"{len(entradas)} descarga(s)"
            if len(entradas) != total:
                etiqueta += f" de {total}"
            self._hist_title.configure(text=etiqueta)

        for w in self._hist_rows:
            w.destroy()
        self._hist_rows.clear()

        # El historial se mira de vez en cuando, no en cada tick: aquí sí se
        # pueden recrear las filas. Aun así se limita lo que se pinta de golpe.
        for i, entrada in enumerate(entradas[:200]):
            self._hist_rows.append(self._history_card(i, entrada))

        if len(entradas) > 200:
            aviso = ctk.CTkLabel(
                self._hist_scroll,
                text=f"…y {len(entradas) - 200} más. Usa el buscador para acotar.",
                font=("", 11), text_color=TEXT_MUT)
            aviso.grid(row=200, column=0, pady=12)
            self._hist_rows.append(aviso)

    def _history_card(self, i: int, entrada: dict) -> ctk.CTkFrame:
        card = ctk.CTkFrame(self._hist_scroll, fg_color=BG_CARD, corner_radius=8,
                            border_width=1, border_color=BORDER)
        card.grid(row=i, column=0, sticky="ew", pady=3, padx=4)
        card.grid_columnconfigure(1, weight=1)

        fmt = entrada.get("fmt", "")
        ctk.CTkLabel(card, text=pretty_format(fmt), font=("", 10, "bold"),
                     text_color=ACCENT2, width=104
                     ).grid(row=0, column=0, rowspan=3, padx=(10, 6))

        ctk.CTkLabel(card, text=(entrada.get("title") or "(sin título)")[:84],
                     font=("", 12), text_color=TEXT_PRI, anchor="w"
                     ).grid(row=0, column=1, sticky="ew", pady=(8, 0))

        ruta = entrada.get("path") or ""
        existe = bool(ruta) and Path(ruta).exists()
        cuando = time.strftime("%d/%m/%Y %H:%M",
                               time.localtime(entrada.get("ts", 0)))
        pie = f"{cuando}  ·  {fmt_size(entrada.get('size'))}"
        if ruta:
            pie += f"  ·  {ruta}"
        if not existe:
            pie += "   ⚠ el archivo ya no está"
        ctk.CTkLabel(card, text=pie[:130], font=("", 9),
                     text_color=TEXT_MUT if existe else ORANGE, anchor="w"
                     ).grid(row=1, column=1, sticky="ew")

        url = entrada.get("url") or ""
        enlace = ctk.CTkLabel(card, text=url or "(sin enlace guardado)",
                              font=("", 10), anchor="w",
                              text_color=BLUE if url else TEXT_MUT)
        enlace.grid(row=2, column=1, sticky="ew", pady=(0, 8))
        if url:
            enlace.configure(cursor="hand2")
            enlace.bind("<Button-1>", lambda _e, u=url: self._copy_link(u))

        acciones = ctk.CTkFrame(card, fg_color="transparent")
        acciones.grid(row=0, column=2, rowspan=3, padx=10, pady=6)

        botones = [("Copiar enlace", lambda: self._copy_link(url), bool(url)),
                   ("A la cola",     lambda: self._history_to_queue(entrada), bool(url)),
                   ("Convertir",     lambda: self._history_to_converter(entrada), existe),
                   ("Carpeta",       lambda: self._reveal(ruta), existe),
                   ("Olvidar",       lambda: self._forget_history(entrada), True)]
        for i_b, (txt, cmd, activo) in enumerate(botones):
            ctk.CTkButton(acciones, text=txt, width=94, height=24, font=("", 10),
                          fg_color="transparent", border_width=1,
                          border_color=BORDER,
                          text_color=TEXT_SEC if activo else TEXT_MUT,
                          hover_color=BG_INPUT, command=cmd,
                          state="normal" if activo else "disabled"
                          ).grid(row=i_b // 3, column=i_b % 3, padx=2, pady=2)
        return card

    # -- acciones del historial ------------------------------------------------
    def _copy_link(self, url: str):
        if not url:
            return
        self.clipboard_clear()
        self.clipboard_append(url)
        self._set_status(f"Enlace copiado: {url[:70]}", GREEN)

    def _copy_all_links(self):
        enlaces = [e["url"] for e in self._filtered_history() if e.get("url")]
        if not enlaces:
            self._set_status("No hay enlaces que copiar", ORANGE)
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(enlaces))
        self._set_status(f"{len(enlaces)} enlace(s) copiados al portapapeles", GREEN)

    def _export_history(self, modo: str):
        """Guarda el historial filtrado: .txt solo enlaces, .csv con todo."""
        from tkinter import filedialog
        entradas = self._filtered_history()
        if not entradas:
            self._set_status("No hay nada que exportar", ORANGE)
            return

        if modo == "txt":
            ruta = filedialog.asksaveasfilename(
                title="Guardar los enlaces", defaultextension=".txt",
                initialfile="ytdown-enlaces.txt",
                filetypes=[("Archivo de texto", "*.txt")])
        else:
            ruta = filedialog.asksaveasfilename(
                title="Guardar el historial", defaultextension=".csv",
                initialfile="ytdown-historial.csv",
                filetypes=[("Hoja de cálculo CSV", "*.csv")])
        if not ruta:
            return

        try:
            escritas = self._write_history_file(Path(ruta), modo, entradas)
        except OSError as e:
            self._set_status(f"No se pudo escribir el archivo: {e}", RED_ERR)
            return
        self._set_status(f"{escritas} entrada(s) exportadas a {ruta}", GREEN)

    @staticmethod
    def _write_history_file(ruta: Path, modo: str, entradas: list[dict]) -> int:
        """Escribe el archivo de exportación. Separado para poder probarlo."""
        ruta.parent.mkdir(parents=True, exist_ok=True)
        if modo == "txt":
            enlaces = [e["url"] for e in entradas if e.get("url")]
            ruta.write_text("\n".join(enlaces) + "\n", encoding="utf-8")
            return len(enlaces)

        import csv
        # utf-8-sig para que Excel no destroce los acentos al abrirlo.
        with open(ruta, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["Titulo", "Enlace", "Formato", "Fecha", "Tamano", "Archivo"])
            for e in entradas:
                w.writerow([
                    e.get("title", ""),
                    e.get("url", ""),
                    pretty_format(e.get("fmt", "")),
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(e.get("ts", 0))),
                    e.get("size", 0),
                    e.get("path", ""),
                ])
        return len(entradas)

    def _history_to_queue(self, entrada: dict):
        """Vuelve a poner una descarga en la cola, ya marcada para bajar."""
        url = entrada.get("url") or ""
        if not url:
            return
        self._tabs.set(TAB_DESCARGAR)
        self._url_box.set_text(url)
        self._add_to_queue()
        # Viene del historial, así que entró marcada como repetida y
        # desmarcada. Si la pidió expresamente, la queremos marcada.
        clave = video_key(url)
        for item in self._q.items():
            if item["key"] == clave:
                item["selected"] = True
        self._render_queue()

    def _history_to_converter(self, entrada: dict):
        ruta = entrada.get("path") or ""
        if not ruta or not Path(ruta).exists():
            return
        nuevos, _rep = self._files.add([ruta])
        self._cv_scan_root = None
        self._tabs.set(TAB_CONVERTIR)
        self._cv_source_lbl.configure(
            text=f"{len(self._files)} archivo(s) en la lista", text_color=TEXT_SEC)
        self._render_files()
        self._cv_banner.show(
            f"'{Path(ruta).name}' añadido a la lista de conversión."
            if nuevos else f"'{Path(ruta).name}' ya estaba en la lista.",
            "ok" if nuevos else "info")

    def _reveal(self, ruta: str):
        """Abre el explorador con el archivo ya seleccionado."""
        import subprocess
        destino = Path(ruta)
        if not destino.exists():
            self._set_status("Ese archivo ya no existe", ORANGE)
            return
        subprocess.Popen(["explorer", "/select,", str(destino)])

    def _forget_history(self, entrada: dict):
        history.forget(entrada.get("video", ""), entrada.get("fmt"))
        self._render_history()
        self._set_status("Quitado del historial — se podrá volver a descargar",
                         TEXT_SEC)

    def _prune_history(self):
        n = history.prune_missing()
        self._render_history()
        self._set_status(
            f"{n} entrada(s) sin archivo salieron del historial" if n
            else "Todos los archivos del historial siguen en su sitio",
            GREEN if n else TEXT_SEC)

    def _clear_history(self):
        from tkinter import messagebox
        if not len(history):
            self._set_status("El historial ya está vacío", TEXT_SEC)
            return
        if not messagebox.askyesno(
                "Vaciar el historial",
                f"Se van a olvidar {len(history)} descargas.\n\n"
                "Los archivos NO se borran: solo se pierde el registro, así que "
                "la app volverá a permitir descargarlas otra vez.\n\n¿Seguir?",
                icon="warning", parent=self):
            return
        n = history.clear()
        self._render_history()
        self._set_status(f"Historial vaciado ({n} entradas)", ORANGE)

    # ======================================================================
    # Cierre
    # ======================================================================
    def _on_close(self):
        if self._dl_manager.running:
            self._dl_manager.cancel()
        if self._converter.running:
            self._converter.cancel()
        self._analyzer.stop()
        # El archivo de cookies es una sesión de YouTube abierta en texto
        # plano dentro de %TEMP%. Existía cleanup() pero nadie lo llamaba, así
        # que se quedaba ahí para siempre después de cerrar.
        cookies_cleanup()
        history.save(force=True)
        cfg_mod.save({
            **self._cfg,   # preserva claves que no maneja la UI (last_update_check…)
            "folder":               self.folder,
            "quality":              self.sel_quality.get(),
            "format":               self.sel_format.get(),
            "cookies_from_browser": self.sel_browser.get(),
            "normalize_audio":      self.opt_normalize.get(),
            "sponsor_block":        self.opt_sponsor.get(),
            "embed_chapters":       self.opt_chapters.get(),
            "rate_limit":           self.rate_limit_var.get().strip(),
            "skip_downloaded":      self.opt_skip_done.get(),
            "speed":                self._speed(),
            "video_resolution":     RES_BY_LABEL.get(self.vid_resolution.get(),
                                                     "1080"),
            "video_codec":          CODEC_BY_LABEL.get(self.vid_codec.get(),
                                                       "compatible"),
            "video_container":      CONT_BY_LABEL.get(self.vid_container.get(),
                                                      "mp4"),
            "video_sound":          self.vid_sound.get(),
            "video_force_compat":   self.vid_force.get(),
            "convert_format":       self.cv_format.get(),
            "convert_bitrate":      self.cv_bitrate.get(),
            "convert_normalize":    self.cv_normalize.get(),
            "convert_metadata":     self.cv_metadata.get(),
            "convert_cover":        self.cv_cover.get(),
            "convert_dest_mode":    self.cv_dest_mode.get(),
            "convert_out_folder":   self.cv_out_folder,
            "convert_keep_tree":    self.cv_keep_tree.get(),
            "convert_collision":    COLISIONES.get(self.cv_collision.get(),
                                                   "renombrar"),
            "convert_recursive":    self.cv_recursive.get(),
            "convert_from_video":   self.cv_from_video.get(),
        })
        self.destroy()
