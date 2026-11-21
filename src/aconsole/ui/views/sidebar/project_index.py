"""Project index navigation in the main content pane."""

from __future__ import annotations

from collections.abc import Callable

import gi
from msgspec.structs import replace

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from aconsole.contracts.app import ProjectIndexItem, ProjectIndexParams, ProjectIndexView  # noqa: E402
from aconsole.ui.bootstrap import spawn_task  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.i18n import _ as translate  # noqa: E402
from aconsole.ui.views.sidebar.network_map import Link, NetworkMap, Point  # noqa: E402


class ProjectIndexPage(Gtk.Box):
    def __init__(self, client: CoreClient, base: ProjectIndexParams, on_back: Callable[[], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=8, margin_bottom=8,
                         margin_start=12, margin_end=12)
        self._client: CoreClient = client
        self._on_back: Callable[[], None] = on_back
        self._base: ProjectIndexParams = base
        self._request: ProjectIndexParams = base
        self._generation: int = 0
        self._items: dict[str, ProjectIndexItem] = {}
        toolbar = Gtk.Box(spacing=8, margin_end=42)
        back = Gtk.Button(label=translate("plan.back"))
        _ = back.connect("clicked", self._back)
        toolbar.append(back)
        overview = Gtk.Button(label=translate("graph.overview"))
        _ = overview.connect("clicked", self._overview)
        toolbar.append(overview)
        self._search: Gtk.SearchEntry = Gtk.SearchEntry(hexpand=True, placeholder_text=translate("graph.search"))
        _ = self._search.connect("activate", self._find)
        toolbar.append(self._search)
        self._more: Gtk.Button = Gtk.Button(icon_name="go-next-symbolic", tooltip_text=translate("graph.more"))
        self._more.set_sensitive(False)
        _ = self._more.connect("clicked", self._next)
        toolbar.append(self._more)
        self._lint: Gtk.Button = Gtk.Button(label="Ruff")
        self._lint.set_sensitive(False)
        _ = self._lint.connect("clicked", self._run_lint)
        toolbar.append(self._lint)
        self.append(toolbar)
        self._map: NetworkMap = NetworkMap(self._select)
        self.append(self._map)
        self._status: Gtk.Label = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.append(self._status)
        self._details: Gtk.TextView = Gtk.TextView(editable=False, cursor_visible=False, monospace=True)
        self._details.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._detail_scroll: Gtk.ScrolledWindow = Gtk.ScrolledWindow(min_content_height=130)
        self._detail_scroll.set_child(self._details)
        self._detail_scroll.set_visible(False)
        self.append(self._detail_scroll)
        self._load(base)

    def _load(self, params: ProjectIndexParams) -> None:
        self._generation += 1
        self._request = params
        self._status.set_text(translate("graph.loading"))
        self._more.set_sensitive(False)
        self._lint.set_sensitive(False)
        _ = spawn_task(self._fetch(params, self._generation))

    async def _fetch(self, params: ProjectIndexParams, generation: int) -> None:
        try:
            page = await self._client.request("project/index", params, ProjectIndexView)
        except Exception as exc:  # noqa: BLE001 -- show the core refusal in the view
            if generation == self._generation:
                self._status.set_text(str(exc))
            return
        if generation != self._generation:
            return
        self._request = replace(params, cursor=page.cursor)
        self._items = {item.id: item for item in page.items}
        self._map.set_graph(
            tuple(Point(item.id, item.name, item.kind == "directory") for item in page.items),
            tuple(Link(link.source, link.target, link.kind) for link in page.links),
        )
        self._more.set_sensitive(page.cursor is not None)
        self._lint.set_sensitive(bool(params.anchor and params.anchor.endswith((".py", ".pyi"))))
        self._status.set_text(self._subtitle(params, page.state))
        chunks = [page.excerpt] if page.excerpt else []
        if page.lint_state is not None:
            chunks.append(f"Ruff · {translate(f'graph.lint.{page.lint_state}')}")
            chunks.extend(page.diagnostics)
        self._details.get_buffer().set_text("\n".join(chunks))
        self._detail_scroll.set_visible(bool(chunks))

    def _select(self, identifier: str) -> None:
        item = self._items.get(identifier)
        if item is None:
            return
        self._map.highlight(identifier)
        if item.kind == "directory":
            self._load(replace(self._base, scope=item.path, query="", anchor=None))
        elif self._request.anchor != item.path and item.kind != "submodule":
            self._load(replace(self._base, anchor=item.path, query="", scope="."))
        else:
            self._status.set_text(item.path if item.start_line is None else f"{item.path}:{item.start_line}")

    @staticmethod
    def _subtitle(params: ProjectIndexParams, state: str) -> str:
        label = params.anchor or params.query or params.scope
        return f"{label} · {translate('graph.partial')}" if state == "partial" else label

    def _overview(self, _button: Gtk.Button) -> None:
        self._load(replace(self._base, scope=".", query="", anchor=None, cursor=None))

    def _back(self, _button: Gtk.Button) -> None:
        self._on_back()

    def _find(self, search: Gtk.SearchEntry) -> None:
        self._load(replace(self._base, query=search.get_text().strip(), anchor=None))

    def _next(self, _button: Gtk.Button) -> None:
        self._load(self._request)

    def _run_lint(self, _button: Gtk.Button) -> None:
        self._load(replace(self._request, cursor=None, diagnose=True))
