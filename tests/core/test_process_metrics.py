"""Only a connected kernel-identified UI peer may supply direction memory metrics."""

import os
import subprocess
from dataclasses import replace
from unittest.mock import patch

import pytest

from aconsole.contracts.common import ClientRole
from aconsole.core.domain.errors import DomainError
from aconsole.core.infrastructure.ipc.ring import EventRing
from aconsole.core.infrastructure.ipc.session import Connection
from aconsole.core.infrastructure.process_metrics import UiProcesses, memory_sample, process_identity
from tests.core.test_ipc_a import _init


def test_memory_sample_checks_identity_and_reports_private_memory() -> None:
    identity = process_identity(os.getpid())
    sample = memory_sample(identity)
    assert 0 < sample["private_mib"] <= sample["rss_mib"]
    assert 0 < sample["pss_mib"] <= sample["rss_mib"]
    with pytest.raises(DomainError, match="NODE_CONTRACT"):
        memory_sample(replace(identity, start_ticks=identity.start_ticks + 1))


def test_unregistered_pid_and_pid_reuse_are_refused() -> None:
    peers = UiProcesses()
    pid = os.getpid()
    with pytest.raises(DomainError, match="NODE_CONTRACT"):
        peers.require(pid)
    identity = peers.register(pid)
    with (patch("aconsole.core.infrastructure.process_metrics.process_identity",
                return_value=replace(identity, start_ticks=identity.start_ticks + 1)),
          pytest.raises(DomainError, match="NODE_CONTRACT")):
        peers.require(pid)
    assert peers.require(pid) == identity


def test_ui_registration_uses_peer_credentials_and_disconnect_is_reference_counted() -> None:
    peers = UiProcesses()
    # initialize.pid is this test, but SO_PEERCRED identifies the other process.
    with subprocess.Popen(["/usr/bin/sleep", "10"]) as child:
        try:
            first = Connection(EventRing(), host_namespace=True, peer_pid=child.pid, ui_processes=peers)
            second = Connection(EventRing(), host_namespace=True, peer_pid=child.pid, ui_processes=peers)
            assert b'"result"' in first.receive(_init())[0]
            assert b'"result"' in second.receive(_init())[0]
            assert peers.require(child.pid).pid == child.pid
            with pytest.raises(DomainError, match="NODE_CONTRACT"):
                peers.require(os.getpid())
            first.close()
            assert peers.require(child.pid).pid == child.pid
            second.close()
            second.close()
            with pytest.raises(DomainError, match="NODE_CONTRACT"):
                peers.require(child.pid)
        finally:
            child.terminate()


def test_cli_connection_does_not_register_as_ui() -> None:
    peers = UiProcesses()
    connection = Connection(EventRing(), host_namespace=True, peer_pid=os.getpid(), ui_processes=peers)
    assert b'"result"' in connection.receive(_init(role=ClientRole.CLI))[0]
    with pytest.raises(DomainError, match="NODE_CONTRACT"):
        peers.require(os.getpid())
