"""Widgets reutilizables de la interfaz.

Las filas de la cola se actualizan *en sitio* (``update_row``) en vez de
destruirse y recrearse. Con una playlist de 200 canciones, reconstruir la
lista entera en cada evento de progreso dejaba la ventana inservible.

Por el mismo motivo las filas están hechas de etiquetas con un binding de
clic en vez de CTkCheckBox/CTkButton: cada uno de esos crea un canvas propio
y se nota cuando hay decenas en pantalla.
"""
import customtkinter as ctk

from .constants import (
    ACCENT, ACCENT2, BG_CARD, BG_INPUT, BG_ROW, BG_HOVER, BORDER,
    TEXT_PRI, TEXT_SEC, TEXT_MUT, GREEN, YELLOW, RED_ERR, ORANGE, BLUE, PURPLE,
    FORMAT_DEFS,
)

CHECK_ON, CHECK_OFF, CHECK_PART = "☑", "☐", "◪"

STATUS_COLORS = {
    "analyzing":   BLUE,
    "pending":     TEXT_MUT,
    "ok":          GREEN,
    "error":       RED_ERR,
    "downloading": YELLOW,
    "retrying":    ORANGE,
    "waiting":     ORANGE,
    "paused":      ORANGE,
    "cancelled":   TEXT_MUT,
    "skipped":     PURPLE,
}
STATUS_TEXT = {
    "analyzing":   "⟳ analizando",
    "pending":     "en espera",
    "ok":          "✓ listo",
    "error":       "✗ error",
    "downloading": "↓ descargando",
    "retrying":    "↻ reintentando",
    "waiting":     "⏲ esperando",
    "paused":      "⏸ pausado",
    "cancelled":   "✕ cancelado",
    "skipped":     "⤼ ya lo tenías",
}
FMT_ICONS = {
    "mp3": "♫", "m4a": "♫", "opus": "♫", "flac": "♫", "wav": "♫",
}


def fmt_icon(fmt: str) -> str:
    """Icono del formato. El video trae la resolución dentro de la clave
    ("video_1080_mp4"), así que no vale un simple lookup."""
    if fmt in FMT_ICONS:
        return "♫"
    if fmt.endswith("_mudo"):
        return "▶"          # video sin sonido
    if fmt.startswith(("video", "mp4_")):
        return "▶"
    return "•"


def _clickable(widget, handler) -> None:
    widget.configure(cursor="hand2")
    widget.bind("<Button-1>", lambda _e: handler())


class FormatSelector(ctk.CTkFrame):
    """Rejilla de tarjetas de formato.

    ``defs`` permite reutilizarla con otras listas —el conversor tiene las
    suyas— manteniendo el mismo aspecto y comportamiento.
    """

    def __init__(self, parent, var: ctk.StringVar, defs=None, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self.var = var
        self._defs = defs if defs is not None else FORMAT_DEFS
        self._cards: dict[str, tuple] = {}
        self._build()
        var.trace_add("write", lambda *_: self._refresh())

    def _build(self):
        for i, (val, label, ico, desc) in enumerate(self._defs):
            self.grid_columnconfigure(i, weight=1)
            card = ctk.CTkFrame(self, fg_color=BG_CARD, corner_radius=10,
                                border_width=1, border_color=BORDER)
            card.grid(row=0, column=i, padx=(0, 6), sticky="ew")

            lbl_ico = ctk.CTkLabel(card, text=ico, font=("", 20),
                                   text_color=TEXT_SEC)
            lbl_ico.grid(row=0, column=0, pady=(10, 2), padx=12)

            lbl_name = ctk.CTkLabel(card, text=label, font=("", 11, "bold"),
                                    text_color=TEXT_PRI)
            lbl_name.grid(row=1, column=0, padx=12)

            lbl_desc = ctk.CTkLabel(card, text=desc, font=("", 9),
                                    text_color=TEXT_MUT)
            lbl_desc.grid(row=2, column=0, padx=12, pady=(0, 10))

            for w in (card, lbl_ico, lbl_name, lbl_desc):
                w.configure(cursor="hand2")
                w.bind("<Button-1>", lambda e, v=val: self.var.set(v))

            self._cards[val] = (card, lbl_ico)
        self._refresh()

    def _refresh(self):
        sel = self.var.get()
        for val, (card, ico) in self._cards.items():
            active = val == sel
            card.configure(fg_color=ACCENT if active else BG_CARD,
                           border_color=ACCENT if active else BORDER)
            ico.configure(text_color="white" if active else TEXT_SEC)


class URLBox(ctk.CTkTextbox):
    """Textbox multilínea con placeholder, para pegar varios enlaces a la vez."""

    def __init__(self, parent, placeholder: str = "", **kwargs):
        super().__init__(parent, **kwargs)
        self._placeholder = placeholder
        self._showing = False
        self._focused = False
        self._show_placeholder()
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)

    def _show_placeholder(self):
        if not self._placeholder:
            return
        super().delete("1.0", "end")
        super().insert("1.0", self._placeholder)
        self.configure(text_color=TEXT_MUT)
        self._showing = True

    def _on_focus_in(self, _evt=None):
        self._focused = True
        if self._showing:
            super().delete("1.0", "end")
            self.configure(text_color=TEXT_PRI)
            self._showing = False

    def _on_focus_out(self, _evt=None):
        self._focused = False
        if not super().get("1.0", "end").strip():
            self._show_placeholder()

    def get_text(self) -> str:
        """Contenido real (cadena vacía si solo se ve el placeholder)."""
        return "" if self._showing else super().get("1.0", "end").strip()

    def set_text(self, text: str):
        super().delete("1.0", "end")
        self._showing = False
        self.configure(text_color=TEXT_PRI)
        super().insert("1.0", text)

    def clear(self):
        super().delete("1.0", "end")
        self._showing = False
        if not self._focused:
            self._show_placeholder()


class Banner(ctk.CTkFrame):
    """Aviso de una línea, con icono, color y botón de cerrar."""

    TONES = {
        "info":    (BLUE, "ℹ"),
        "ok":      (GREEN, "✓"),
        "warn":    (ORANGE, "⚠"),
        "error":   (RED_ERR, "✕"),
    }

    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color=BG_CARD, corner_radius=10,
                         border_width=1, border_color=BORDER, **kwargs)
        self.grid_columnconfigure(1, weight=1)

        self._icon = ctk.CTkLabel(self, text="", font=("", 14), width=24)
        self._icon.grid(row=0, column=0, padx=(12, 6), pady=9)

        self._text = ctk.CTkLabel(self, text="", font=("", 11),
                                  text_color=TEXT_PRI, anchor="w",
                                  justify="left", wraplength=560)
        self._text.grid(row=0, column=1, sticky="ew")

        self._action = ctk.CTkButton(self, text="", width=110, height=26,
                                     font=("", 11, "bold"))
        self._action.grid(row=0, column=2, padx=6)
        self._action.grid_remove()

        close = ctk.CTkLabel(self, text="✕", font=("", 12), text_color=TEXT_MUT,
                             width=24)
        close.grid(row=0, column=3, padx=(0, 10))
        _clickable(close, self.hide)

        self._visible = False

    def show(self, text: str, tone: str = "info", *,
             action: str = "", on_action=None):
        color, icon = self.TONES.get(tone, self.TONES["info"])
        self._icon.configure(text=icon, text_color=color)
        self._text.configure(text=text)
        self.configure(border_color=color)
        if action and on_action:
            self._action.configure(text=action, command=on_action,
                                   fg_color=color, hover_color=color)
            self._action.grid()
        else:
            self._action.grid_remove()
        if not self._visible:
            self.grid()
            self._visible = True

    def hide(self):
        if self._visible:
            self.grid_remove()
            self._visible = False


class QueueRow(ctk.CTkFrame):
    """Fila de la cola. Sirve para un video, para una playlist o para 'ver más'.

    Una misma instancia se recicla para nodos distintos: ``update_row`` la
    reconfigura en vez de destruirla.
    """

    def __init__(self, parent, callbacks: dict, **kwargs):
        super().__init__(parent, fg_color=BG_CARD, corner_radius=0, **kwargs)
        self._cb = callbacks
        self._node_id: int | None = None
        self._kind = "item"
        self._base_bg = BG_CARD
        self._bar = None
        # Firma de lo último que se pintó. Cada .configure() de customtkinter
        # redibuja el widget, así que una fila que no cambió no se toca.
        self._sig: tuple | None = None
        self._grid_row: int | None = None

        self.grid_columnconfigure(4, weight=1)

        self._arrow = ctk.CTkLabel(self, text="", font=("", 12), width=18,
                                   text_color=TEXT_SEC)
        self._arrow.grid(row=0, column=0, rowspan=2, padx=(8, 0))
        _clickable(self._arrow, self._on_arrow)

        self._check = ctk.CTkLabel(self, text=CHECK_ON, font=("", 14), width=22,
                                   text_color=ACCENT)
        self._check.grid(row=0, column=1, rowspan=2, padx=(2, 2))
        _clickable(self._check, self._on_check)

        self._index = ctk.CTkLabel(self, text="", font=("", 10),
                                   text_color=TEXT_MUT, width=28)
        self._index.grid(row=0, column=2, rowspan=2)

        self._icon = ctk.CTkLabel(self, text="", font=("", 14),
                                  text_color=ACCENT, width=24)
        self._icon.grid(row=0, column=3, rowspan=2, padx=(2, 6), pady=6)

        self._title = ctk.CTkLabel(self, text="", font=("", 12),
                                   text_color=TEXT_PRI, anchor="w")
        self._title.grid(row=0, column=4, sticky="ew", pady=(7, 0))

        self._detail = ctk.CTkLabel(self, text="", font=("", 10),
                                    text_color=TEXT_MUT, anchor="w")
        self._detail.grid(row=1, column=4, sticky="ew", pady=(0, 7))

        self._status = ctk.CTkLabel(self, text="", font=("", 11),
                                    text_color=TEXT_MUT, width=104)
        self._status.grid(row=0, column=5, rowspan=2, padx=8)

        self._remove = ctk.CTkLabel(self, text="✕", font=("", 12),
                                    text_color=TEXT_MUT, width=26)
        self._remove.grid(row=0, column=6, rowspan=2, padx=(0, 8))
        _clickable(self._remove, self._on_remove)

        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    # -- eventos ---------------------------------------------------------------
    def _on_arrow(self):
        if self._kind == "group" and self._node_id is not None:
            self._cb["toggle"](self._node_id)

    def _on_check(self):
        if self._node_id is not None and self._kind != "more":
            self._cb["select"](self._node_id)

    def _on_remove(self):
        if self._node_id is not None:
            if self._kind == "more":
                self._cb["more"](self._node_id)
            else:
                self._cb["remove"](self._node_id)

    def _on_enter(self, _e=None):
        self.configure(fg_color=BG_HOVER)

    def _on_leave(self, _e=None):
        self.configure(fg_color=self._base_bg)

    # -- pintado ---------------------------------------------------------------
    @staticmethod
    def _signature(row: dict, stripe: int, model) -> tuple:
        """Todo lo que se ve en la fila, resumido. Si no cambia, no se repinta."""
        node, kind = row["node"], row["kind"]
        base = (node["id"], kind, stripe, row["depth"], row["index"])
        if kind == "more":
            return base
        if kind == "group":
            sel, total = model.group_selection(node)
            kids = node["children"]
            return base + (
                node["title"], node["analyzed"], node["expanded"], node.get("error", ""),
                sel, total, model.group_status(node),
                sum(1 for c in kids if c["duplicate"]),
                sum(1 for c in kids if c["status"] == "ok"),
            )
        return base + (
            node["title"], node.get("detail", ""), node.get("error", ""),
            node["status"], node["analyzed"], node["selected"],
            node["duplicate"], node.get("fmt", ""),
        )

    def update_row(self, row: dict, stripe: int, model) -> bool:
        """Repinta la fila si hace falta. Devuelve True si hubo cambios."""
        sig = self._signature(row, stripe, model)
        if sig == self._sig:
            return False
        self._sig = sig

        node, kind = row["node"], row["kind"]
        self._node_id = node["id"]
        self._kind = kind

        bg = BG_ROW if stripe % 2 else BG_CARD
        if bg != self._base_bg:
            self._base_bg = bg
            self.configure(fg_color=bg)

        pad_left = 8 + (18 if row["depth"] else 0)
        self._arrow.grid_configure(padx=(pad_left, 0))

        if kind == "more":
            self._show_more_row(row)
        elif kind == "group":
            self._show_group_row(node, model)
        else:
            self._show_item_row(node, row["index"])
        return True

    def place_at(self, index: int) -> None:
        """Coloca la fila en su posición, sin re-gridear si ya está ahí."""
        if self._grid_row != index or not self.winfo_ismapped():
            self.grid(row=index, column=0, sticky="ew")
            self._grid_row = index

    def hide(self) -> None:
        if self._grid_row is not None:
            self.grid_remove()
            self._grid_row = None
            self._sig = None

    def _show_more_row(self, row: dict) -> None:
        self._arrow.configure(text="")
        self._check.configure(text="")
        self._index.configure(text="")
        self._icon.configure(text="⋯", text_color=TEXT_MUT)
        self._title.configure(text=f"Ver {row['index']} videos más",
                              text_color=BLUE)
        self._detail.configure(text="")
        self._status.configure(text="")
        self._remove.configure(text="▾", text_color=BLUE)
        self._hide_bar()

    def _show_group_row(self, group: dict, model) -> None:
        sel, total = model.group_selection(group)
        status = model.group_status(group)

        self._arrow.configure(text="▾" if group["expanded"] else "▸",
                              text_color=TEXT_PRI)
        if total and sel == total:
            mark, color = CHECK_ON, ACCENT
        elif sel:
            mark, color = CHECK_PART, ACCENT
        else:
            mark, color = CHECK_OFF, TEXT_MUT
        self._check.configure(text=mark, text_color=color)

        self._index.configure(text="")
        self._icon.configure(text="≡", text_color=ACCENT2)
        self._title.configure(text=_clip(group["title"], 70), text_color=TEXT_PRI)

        if not group["analyzed"]:
            detail = "leyendo la playlist…"
        elif not total:
            detail = group.get("error") or "playlist vacía o no accesible"
        else:
            dup = sum(1 for c in group["children"] if c["duplicate"])
            done = sum(1 for c in group["children"] if c["status"] == "ok")
            bits = [f"{total} videos", f"{sel} seleccionados"]
            if dup:
                bits.append(f"{dup} ya descargados")
            if done:
                bits.append(f"{done} completados")
            detail = "  ·  ".join(bits)
        self._detail.configure(text=detail, text_color=TEXT_SEC)

        self._status.configure(text=STATUS_TEXT.get(status, ""),
                               text_color=STATUS_COLORS.get(status, TEXT_MUT))
        self._remove.configure(text="✕", text_color=TEXT_MUT)
        self._hide_bar()

    def _show_item_row(self, item: dict, index: int) -> None:
        status = _display_status(item)

        self._arrow.configure(text="")
        self._check.configure(text=CHECK_ON if item["selected"] else CHECK_OFF,
                              text_color=ACCENT if item["selected"] else TEXT_MUT)
        self._index.configure(text=str(index))
        self._icon.configure(text=fmt_icon(item.get("fmt", "")),
                             text_color=ACCENT if item["selected"] else TEXT_MUT)
        self._title.configure(
            text=_clip(item["title"], 72),
            text_color=TEXT_PRI if item["selected"] else TEXT_MUT)

        if status == "error" and item.get("error"):
            detail, dcolor = item["error"], RED_ERR
        elif item["duplicate"]:
            detail, dcolor = (item.get("detail") or "ya descargado antes"), PURPLE
        else:
            detail, dcolor = item.get("detail", ""), TEXT_MUT
        self._detail.configure(text=_clip(str(detail), 96), text_color=dcolor)

        self._status.configure(text=STATUS_TEXT.get(status, ""),
                               text_color=STATUS_COLORS.get(status, TEXT_MUT))
        self._remove.configure(text="✕", text_color=TEXT_MUT)

        if status == "downloading":
            self._show_bar(item.get("progress", 0.0))
        else:
            self._hide_bar()

    # -- barra de progreso por fila (se crea solo cuando hace falta) ------------
    def _show_bar(self, pct: float) -> None:
        if self._bar is None:
            self._bar = ctk.CTkProgressBar(self, height=3, corner_radius=2,
                                           fg_color="#2A2A2A",
                                           progress_color=ACCENT)
        self._bar.grid(row=2, column=4, columnspan=2, sticky="ew", pady=(0, 6))
        self._bar.set(max(0.0, min(1.0, pct)))

    def _hide_bar(self) -> None:
        if self._bar is not None:
            self._bar.grid_remove()

    def set_progress(self, pct: float) -> None:
        """Actualiza solo la barra, sin repintar la fila entera."""
        if self._bar is not None and self._bar.winfo_ismapped():
            self._bar.set(max(0.0, min(1.0, pct)))


def _display_status(item: dict) -> str:
    """Un item pendiente que aún no tiene título está, en realidad, analizándose."""
    if item["status"] == "pending" and not item.get("analyzed"):
        return "analyzing"
    return item["status"]


def _clip(text: str, n: int) -> str:
    text = str(text)
    return text if len(text) <= n else text[: n - 1] + "…"


CONVERT_STATUS_TEXT = {
    "pending":    "en espera",
    "converting": "⚙ convirtiendo",
    "ok":         "✓ listo",
    "error":      "✗ error",
    "skipped":    "⤼ omitido",
}
CONVERT_STATUS_COLORS = {
    "pending":    TEXT_MUT,
    "converting": YELLOW,
    "ok":         GREEN,
    "error":      RED_ERR,
    "skipped":    PURPLE,
}


class FileRow(ctk.CTkFrame):
    """Fila de un archivo en la lista del conversor.

    Misma idea que QueueRow: se recicla y solo se repinta si algo cambió.
    """

    def __init__(self, parent, callbacks: dict, **kwargs):
        super().__init__(parent, fg_color=BG_CARD, corner_radius=0, **kwargs)
        self._cb = callbacks
        self._item_id: int | None = None
        self._sig: tuple | None = None
        self._grid_row: int | None = None
        self._base_bg = BG_CARD
        self._bar = None

        self.grid_columnconfigure(3, weight=1)

        self._check = ctk.CTkLabel(self, text=CHECK_ON, font=("", 14), width=22,
                                   text_color=ACCENT)
        self._check.grid(row=0, column=0, rowspan=2, padx=(10, 2))
        _clickable(self._check, self._on_check)

        self._index = ctk.CTkLabel(self, text="", font=("", 10),
                                   text_color=TEXT_MUT, width=28)
        self._index.grid(row=0, column=1, rowspan=2)

        self._icon = ctk.CTkLabel(self, text="♪", font=("", 13),
                                  text_color=ACCENT2, width=22)
        self._icon.grid(row=0, column=2, rowspan=2, padx=(2, 6), pady=6)

        self._name = ctk.CTkLabel(self, text="", font=("", 12),
                                  text_color=TEXT_PRI, anchor="w")
        self._name.grid(row=0, column=3, sticky="ew", pady=(7, 0))

        self._detail = ctk.CTkLabel(self, text="", font=("", 10),
                                    text_color=TEXT_MUT, anchor="w")
        self._detail.grid(row=1, column=3, sticky="ew", pady=(0, 7))

        self._status = ctk.CTkLabel(self, text="", font=("", 11),
                                    text_color=TEXT_MUT, width=100)
        self._status.grid(row=0, column=4, rowspan=2, padx=8)

        self._remove = ctk.CTkLabel(self, text="✕", font=("", 12),
                                    text_color=TEXT_MUT, width=26)
        self._remove.grid(row=0, column=5, rowspan=2, padx=(0, 8))
        _clickable(self._remove, self._on_remove)

        self.bind("<Enter>", lambda _e: self.configure(fg_color=BG_HOVER))
        self.bind("<Leave>", lambda _e: self.configure(fg_color=self._base_bg))

    def _on_check(self):
        if self._item_id is not None:
            self._cb["select"](self._item_id)

    def _on_remove(self):
        if self._item_id is not None:
            self._cb["remove"](self._item_id)

    def update_row(self, item: dict, index: int) -> bool:
        sig = (item["id"], index, item["name"], item["selected"], item["status"],
               item.get("detail", ""), item.get("error", ""))
        if sig == self._sig:
            return False
        self._sig = sig
        self._item_id = item["id"]

        bg = BG_ROW if index % 2 else BG_CARD
        if bg != self._base_bg:
            self._base_bg = bg
            self.configure(fg_color=bg)

        sel = item["selected"]
        self._check.configure(text=CHECK_ON if sel else CHECK_OFF,
                              text_color=ACCENT if sel else TEXT_MUT)
        self._index.configure(text=str(index + 1))
        self._icon.configure(text_color=ACCENT2 if sel else TEXT_MUT)
        self._name.configure(text=_clip(item["name"], 64),
                             text_color=TEXT_PRI if sel else TEXT_MUT)

        if item["status"] == "error" and item.get("error"):
            detalle, color = item["error"], RED_ERR
        else:
            detalle, color = item.get("detail", ""), TEXT_MUT
        self._detail.configure(text=_clip(str(detalle), 92), text_color=color)

        st = item["status"]
        self._status.configure(text=CONVERT_STATUS_TEXT.get(st, ""),
                               text_color=CONVERT_STATUS_COLORS.get(st, TEXT_MUT))

        if st == "converting":
            if self._bar is None:
                self._bar = ctk.CTkProgressBar(self, height=3, corner_radius=2,
                                               fg_color="#2A2A2A",
                                               progress_color=ACCENT2)
            self._bar.grid(row=2, column=3, columnspan=2, sticky="ew", pady=(0, 6))
            self._bar.set(item.get("progress", 0.0))
        elif self._bar is not None:
            self._bar.grid_remove()
        return True

    def set_progress(self, pct: float) -> None:
        if self._bar is not None and self._bar.winfo_ismapped():
            self._bar.set(max(0.0, min(1.0, pct)))

    def place_at(self, index: int) -> None:
        if self._grid_row != index or not self.winfo_ismapped():
            self.grid(row=index, column=0, sticky="ew")
            self._grid_row = index

    def hide(self) -> None:
        if self._grid_row is not None:
            self.grid_remove()
            self._grid_row = None
            self._sig = None


class ProgressCard(ctk.CTkFrame):
    """Barra de progreso global + título + velocidad/ETA."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color=BG_CARD, corner_radius=10,
                         border_width=1, border_color=BORDER, **kwargs)
        self.grid_columnconfigure(0, weight=1)

        self.title_lbl = ctk.CTkLabel(
            self, text="", font=("", 12, "bold"),
            text_color=TEXT_PRI, anchor="w", wraplength=680)
        self.title_lbl.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 4))

        self.pct_lbl = ctk.CTkLabel(self, text="", font=("", 12, "bold"),
                                    text_color=ACCENT, width=54)
        self.pct_lbl.grid(row=0, column=1, padx=(0, 14), pady=(12, 4))

        self.bar = ctk.CTkProgressBar(
            self, height=6, fg_color="#2a2a2a", progress_color=ACCENT)
        self.bar.grid(row=1, column=0, columnspan=2, sticky="ew", padx=14, pady=4)
        self.bar.set(0)

        self.info_lbl = ctk.CTkLabel(
            self, text="", font=("", 11), text_color=TEXT_SEC, anchor="w")
        self.info_lbl.grid(row=2, column=0, columnspan=2, sticky="ew",
                           padx=14, pady=(0, 12))

    def update(self, title: str = "", info: str = "", pct: float | None = None):
        if title:
            self.title_lbl.configure(text=_clip(title, 92))
        if info:
            self.info_lbl.configure(text=info)
        if pct is not None:
            self.bar.set(max(0.0, min(1.0, pct)))
            self.pct_lbl.configure(text=f"{int(pct * 100)}%")

    def set_color(self, color: str):
        self.bar.configure(progress_color=color)
        self.pct_lbl.configure(text_color=color)


class ChipBar(ctk.CTkFrame):
    """Fila de contadores con color: total / pendientes / listos / errores."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, fg_color="transparent", **kwargs)
        self._chips: dict[str, ctk.CTkLabel] = {}

    def render(self, chips: list[tuple[str, str, str]]) -> None:
        """chips: lista de (clave, texto, color). Reutiliza las etiquetas."""
        for i, (key, text, color) in enumerate(chips):
            lbl = self._chips.get(key)
            if lbl is None:
                # CTkLabel fija padx/pady en su Label interno: el aire va
                # en el grid, no en el constructor.
                lbl = ctk.CTkLabel(self, text="", font=("", 11), height=22,
                                   fg_color=BG_INPUT, corner_radius=8)
                self._chips[key] = lbl
            lbl.configure(text=f"  {text}  ", text_color=color)
            lbl.grid(row=0, column=i, padx=(0, 6), sticky="w")
        for key, lbl in self._chips.items():
            if key not in {c[0] for c in chips}:
                lbl.grid_remove()
