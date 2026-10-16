"""Modelo de datos de la cola de descargas.

Separado de la UI a propósito: la cola tiene reglas propias (deduplicación,
grupos de playlist, selección) y probarlas o cambiarlas no debería obligar a
tocar widgets.

La cola es una lista de *nodos* de nivel superior. Un nodo es:

  - un ``item``  -> un video suelto
  - un ``group`` -> una playlist, con sus videos dentro de ``children``

Los grupos se pintan plegados con una flecha; al desplegarlos se ven sus hijos,
cada uno con su casilla para elegir si entra o no en la descarga.
"""
import itertools
import logging
from typing import Iterator

from .history import shared as history
from .info import video_key

log = logging.getLogger("ytdown.queue")

# Estados posibles de un item.
PENDING, DOWNLOADING, OK, ERROR = "pending", "downloading", "ok", "error"
RETRYING, WAITING, CANCELLED, SKIPPED = "retrying", "waiting", "cancelled", "skipped"

_ACTIVE = (DOWNLOADING, RETRYING, WAITING)
_RETRYABLE = (PENDING, ERROR, CANCELLED)


class QueueModel:
    """Cola de descargas. No sabe nada de tkinter."""

    def __init__(self) -> None:
        self._nodes: list[dict] = []
        self._ids = itertools.count(1)
        self._index: dict[int, dict] = {}    # id -> nodo/item, para no recorrer la lista

    # -- acceso ----------------------------------------------------------------
    @property
    def nodes(self) -> list[dict]:
        return self._nodes

    def new_id(self) -> int:
        return next(self._ids)

    def by_id(self, node_id: int) -> dict | None:
        return self._index.get(node_id)

    def items(self) -> Iterator[dict]:
        """Todos los videos, estén sueltos o dentro de un grupo."""
        for node in self._nodes:
            if node["kind"] == "group":
                yield from node["children"]
            else:
                yield node

    def __len__(self) -> int:
        return sum(1 for _ in self.items())

    def is_empty(self) -> bool:
        return not self._nodes

    # -- alta ------------------------------------------------------------------
    def _register(self, node: dict) -> dict:
        self._index[node["id"]] = node
        return node

    def make_item(self, url: str, title: str, fmt: str, opts: dict, *,
                  detail: str = "", duration: int = 0,
                  analyzed: bool = False, parent: int | None = None) -> dict:
        return self._register({
            "kind":      "item",
            "id":        self.new_id(),
            "url":       url,
            "key":       video_key(url),
            "title":     title,
            "detail":    detail,
            "duration":  duration,
            "error":     "",
            "fmt":       fmt,
            "opts":      opts,
            "status":    PENDING,
            "analyzed":  analyzed,
            "selected":  True,
            "parent":    parent,
            "duplicate": False,
            "progress":  0.0,
        })

    def add_item(self, item: dict) -> dict:
        self._nodes.append(item)
        return item

    def add_group(self, url: str, title: str, fmt: str) -> dict:
        group = self._register({
            "kind":      "group",
            "id":        self.new_id(),
            "url":       url,
            "key":       video_key(url),
            "title":     title,
            "detail":    "",
            "error":     "",
            "fmt":       fmt,
            "status":    PENDING,
            "analyzed":  False,
            "expanded":  False,
            "children":  [],
            "shown":     25,     # cuántos hijos se pintan antes de "ver más"
        })
        self._nodes.append(group)
        return group

    def fill_group(self, group: dict, children: list[dict]) -> None:
        group["children"] = children
        group["analyzed"] = True
        for child in children:
            child["parent"] = group["id"]
            self._index[child["id"]] = child

    # -- deduplicación ---------------------------------------------------------
    def contains(self, url: str, fmt: str) -> bool:
        """Está este video (en este formato) ya en la cola?

        Se compara por ID de video, no por URL: youtu.be/X y watch?v=X son el
        mismo video aunque el texto difiera.
        """
        key = video_key(url)
        return any(it["key"] == key and it["fmt"] == fmt for it in self.items())

    def mark_duplicates(self, items: list[dict], *, deselect: bool = True) -> int:
        """Marca los que ya estén en el historial. Devuelve cuántos marcó."""
        n = 0
        for item in items:
            if history.has(item["key"], item["fmt"]):
                item["duplicate"] = True
                item["detail"] = "ya descargado antes"
                if deselect:
                    item["selected"] = False
                n += 1
        return n

    # -- selección -------------------------------------------------------------
    def set_selected(self, node_id: int, value: bool) -> None:
        node = self.by_id(node_id)
        if not node:
            return
        if node["kind"] == "group":
            for child in node["children"]:
                if child["status"] not in _ACTIVE:
                    child["selected"] = value
        elif node["status"] not in _ACTIVE:
            node["selected"] = value

    def group_selection(self, group: dict) -> tuple[int, int]:
        """(seleccionados, total) de un grupo."""
        kids = group["children"]
        return sum(1 for c in kids if c["selected"]), len(kids)

    def select_all(self, value: bool, *, only_pending: bool = True) -> None:
        for item in self.items():
            if only_pending and item["status"] not in _RETRYABLE:
                continue
            item["selected"] = value

    def toggle_expanded(self, group_id: int) -> None:
        group = self.by_id(group_id)
        if group and group["kind"] == "group":
            group["expanded"] = not group["expanded"]

    def show_more(self, group_id: int, step: int = 60) -> None:
        group = self.by_id(group_id)
        if group and group["kind"] == "group":
            group["shown"] += step

    # -- baja ------------------------------------------------------------------
    def remove(self, node_id: int) -> tuple[bool, str]:
        """Quita un nodo. Devuelve (ok, motivo_si_no)."""
        node = self.by_id(node_id)
        if not node:
            return False, "ya no está en la cola"

        if node["kind"] == "group":
            if any(c["status"] in _ACTIVE for c in node["children"]):
                return False, "hay un video de esta playlist descargándose"
            for child in node["children"]:
                self._index.pop(child["id"], None)
            self._nodes.remove(node)
            self._index.pop(node_id, None)
            return True, ""

        if node["status"] in _ACTIVE:
            return False, "ese video se está descargando ahora"

        parent = self.by_id(node["parent"]) if node["parent"] else None
        if parent:
            parent["children"].remove(node)
            self._index.pop(node_id, None)
            if not parent["children"]:
                self._nodes.remove(parent)
                self._index.pop(parent["id"], None)
        else:
            self._nodes.remove(node)
            self._index.pop(node_id, None)
        return True, ""

    def remove_where(self, predicate) -> int:
        """Quita los items que cumplan el predicado (y grupos que queden vacíos)."""
        victims = [it["id"] for it in self.items()
                   if predicate(it) and it["status"] not in _ACTIVE]
        return sum(1 for vid in victims if self.remove(vid)[0])

    def clear(self) -> None:
        self._nodes.clear()
        self._index.clear()

    # -- consultas para la descarga --------------------------------------------
    def downloadable(self) -> list[dict]:
        """Items marcados y en un estado que admite (re)intentar la descarga."""
        return [it for it in self.items()
                if it["selected"] and it["status"] in _RETRYABLE]

    def counts(self) -> dict:
        """Resumen para la cabecera de la cola."""
        c = {"total": 0, "selected": 0, "pending": 0, "ok": 0,
             "error": 0, "duplicate": 0, "skipped": 0, "groups": 0}
        for item in self.items():
            c["total"] += 1
            if item["selected"]:
                c["selected"] += 1
            if item["duplicate"]:
                c["duplicate"] += 1
            st = item["status"]
            if st in _RETRYABLE and item["selected"]:
                c["pending"] += 1
            if st == OK:
                c["ok"] += 1
            elif st == ERROR:
                c["error"] += 1
            elif st == SKIPPED:
                c["skipped"] += 1
        c["groups"] = sum(1 for n in self._nodes if n["kind"] == "group")
        return c

    # -- render ----------------------------------------------------------------
    def visible_rows(self) -> list[dict]:
        """Filas a pintar, en orden. Cada una: {node, depth, kind, index}.

        Un grupo plegado ocupa una sola fila; desplegado añade sus hijos (hasta
        ``shown``) y, si quedan más, una fila "ver más".
        """
        rows: list[dict] = []
        num = 0
        for node in self._nodes:
            if node["kind"] != "group":
                num += 1
                rows.append({"node": node, "depth": 0, "kind": "item", "index": num})
                continue

            rows.append({"node": node, "depth": 0, "kind": "group", "index": 0})
            if not node["expanded"]:
                num += len(node["children"])
                continue
            for child in node["children"][:node["shown"]]:
                num += 1
                rows.append({"node": child, "depth": 1, "kind": "item", "index": num})
            hidden = len(node["children"]) - node["shown"]
            if hidden > 0:
                num += hidden
                rows.append({"node": node, "depth": 1, "kind": "more", "index": hidden})
        return rows

    def group_status(self, group: dict) -> str:
        """Estado agregado de un grupo, para pintar su fila de cabecera."""
        kids = group["children"]
        if not group["analyzed"]:
            return "analyzing"
        if not kids:
            return ERROR
        if any(c["status"] in _ACTIVE for c in kids):
            return DOWNLOADING
        if all(c["status"] == OK for c in kids):
            return OK
        if any(c["status"] == ERROR for c in kids):
            return ERROR
        return PENDING
