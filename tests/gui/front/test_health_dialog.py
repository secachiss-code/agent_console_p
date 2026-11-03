"""The health dialog shows what the core reports, and says so when the core does not answer."""

import asyncio
import tempfile

import gi
import pytest

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from aconsole.contracts.app import HealthResult  # noqa: E402
from aconsole.core.infrastructure.fake.server import FakeCore  # noqa: E402
from aconsole.ui.client.core_client import CoreClient  # noqa: E402
from aconsole.ui.viewmodels.health_vm import HealthFacts, HealthViewModel  # noqa: E402
from aconsole.ui.views.health_dialog import HealthDialog  # noqa: E402

_HEALTHY = HealthResult(
    database=True,
    writer_queued=2,
    writer_failed=0,
    writer_last_commit_ms=3.25,
    ipc_answered=40,
    ipc_slowest_ms=12.5,
)


@pytest.fixture(autouse=True)
def _display() -> None:
    if not Gtk.init_check():
        pytest.skip("no display")
    _ = Adw.init()


def _dialog() -> HealthDialog:
    return HealthDialog(HealthViewModel(CoreClient("unused")))


def _trouble(dialog: HealthDialog) -> set[str]:
    return {key for key, label in dialog.values.items() if label.has_css_class("error")}


def test_healthy_numbers_are_shown_without_alarm() -> None:
    dialog = _dialog()
    dialog.show_facts(HealthFacts(core=_HEALTHY, resyncs=1, reconnects=2))
    assert dialog.values["health.queued"].get_label() == "2"
    assert dialog.values["health.commit"].get_label() == "3.2"
    assert dialog.values["health.answered"].get_label() == "40"
    assert dialog.values["health.slowest"].get_label() == "12.5"
    assert dialog.values["health.resyncs"].get_label() == "1"
    assert dialog.values["health.reconnects"].get_label() == "2"
    assert _trouble(dialog) == set()


def test_failed_writes_stand_out_and_clear_when_they_are_gone() -> None:
    dialog = _dialog()
    failing = HealthResult(
        database=True,
        writer_queued=500,
        writer_failed=3,
        writer_last_commit_ms=1.0,
        ipc_answered=1,
        ipc_slowest_ms=2500.0,
    )
    dialog.show_facts(HealthFacts(core=failing, resyncs=0, reconnects=0))
    assert _trouble(dialog) == {"health.queued", "health.failed", "health.slowest"}
    dialog.show_facts(HealthFacts(core=_HEALTHY, resyncs=0, reconnects=0))
    assert _trouble(dialog) == set()


def test_a_core_that_does_not_answer_is_the_finding() -> None:
    dialog = _dialog()
    dialog.show_facts(HealthFacts(core=_HEALTHY, resyncs=0, reconnects=0))
    dialog.show_facts(HealthFacts(core=None, resyncs=4, reconnects=1))
    assert _trouble(dialog) == {"health.core"}
    assert dialog.values["health.queued"].get_label() == "—"
    assert dialog.values["health.resyncs"].get_label() == "4"


async def _load(path: str) -> HealthFacts:
    client = CoreClient(path, timeout_s=2)
    try:
        _ = await client.connect()
        return await HealthViewModel(client).load()
    finally:
        await client.close()


def test_the_view_model_reads_a_core_over_the_socket() -> None:
    core = FakeCore(f"{tempfile.mkdtemp(prefix='aconsole-gui-')}/core.sock", "demo")
    core.start()
    try:
        facts = asyncio.run(_load(core.path))
    finally:
        core.stop()
    assert facts.core is not None
    assert facts.core.database
    assert facts.reconnects == 0


def test_the_view_model_reports_a_core_that_is_gone() -> None:
    async def run() -> HealthFacts:
        client = CoreClient(f"{tempfile.mkdtemp(prefix='aconsole-gui-')}/core.sock", timeout_s=0.2)
        return await HealthViewModel(client).load()

    assert asyncio.run(run()).core is None
