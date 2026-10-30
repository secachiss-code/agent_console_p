"""Cached palette search and execution state, independent of GTK."""

from aconsole.contracts.app import CommandResult, PaletteExecuteParams, PaletteItem, PaletteItemsResult, PaletteQuery
from aconsole.ui.client.core_client import CoreClient
from aconsole.ui.viewmodels.logic.rank import rank_items


class PaletteViewModel:
    def __init__(self, client: CoreClient) -> None:
        self._client: CoreClient = client
        self._generation: int = 0
        self._context: PaletteQuery | None = None
        self._catalog: tuple[PaletteItem, ...] = ()
        self.opened: bool = False
        self.loading: bool = False
        self.executing: bool = False
        self.query: str = ""
        self.items: tuple[PaletteItem, ...] = ()
        self.selected: PaletteItem | None = None
        self.error: Exception | None = None

    async def open(self, context: PaletteQuery) -> None:
        self._generation += 1
        generation = self._generation
        self._context = context
        self._catalog = ()
        self.opened = True
        self.loading = True
        self.query = ""
        self.items = ()
        self.selected = None
        self.error = None
        try:
            result = await self._client.request("palette/items", context, PaletteItemsResult)
        except Exception as error:
            if generation == self._generation:
                self.loading = False
                self.error = error
                raise
            return
        if generation == self._generation:
            self.loading = False
            self._catalog = result.items
            self.items = rank_items(self._catalog, self.query)

    def close(self) -> None:
        self._generation += 1
        self.opened = False
        self.loading = False
        self._context = None
        self._catalog = ()
        self.items = ()
        self.selected = None

    def search(self, query: str) -> None:
        if query != self.query:
            self.selected = None
        self.query = query
        self.items = rank_items(self._catalog, query)

    def select(self, item_id: str) -> None:
        item = next((item for item in self.items if item.id == item_id and item.enabled), None)
        if item is None:
            message = "palette item is unavailable"
            raise ValueError(message)
        self.selected = item

    def back(self) -> None:
        self.selected = None

    async def execute(self, argument: str | None = None) -> CommandResult | None:
        if self.executing:
            return None
        item, context = self.selected, self._context
        if item is None or context is None:
            message = "no palette item selected"
            raise ValueError(message)
        spec = item.argument
        if spec is None and argument is not None:
            message = "invalid palette argument"
            raise ValueError(message)
        if spec is not None and (
            argument is None or (spec.choices and argument not in {choice[0] for choice in spec.choices})
        ):
            message = "invalid palette argument"
            raise ValueError(message)
        generation = self._generation
        self.executing = True
        self.error = None
        try:
            return await self._client.request(
                "palette/execute",
                PaletteExecuteParams(
                    item_id=item.id, tab_id=context.tab_id, project_id=context.project_id, argument=argument,
                ),
                CommandResult,
            )
        except Exception as error:
            if generation == self._generation:
                self.error = error
            raise
        finally:
            self.executing = False
