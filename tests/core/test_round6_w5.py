"""Round 6 W5: a distrobox probe that started under an old ini digest must not cache its PATH.

The fake distrobox blocks its first call on a release file. The test changes the ini while that probe is
still running, so the second probe runs under the new digest, and only then lets the first one finish.
Nothing here depends on timing: every wait is a poll for a file, with a deadline.
"""

from __future__ import annotations

import stat
import threading
import time
from typing import TYPE_CHECKING

from aconsole.core.infrastructure.containers.distrobox_env import DistroboxEnvCache

if TYPE_CHECKING:
    from pathlib import Path

    import pytest

_DEADLINE_S = 5.0
_POLL_S = 0.01
_CONTAINER = "agent-claude"
_FAKE = """#!/bin/sh
# Fake `distrobox enter -d NAME`: the PATH is read when the call starts. The first call waits for release.
work="$W5_DIR"
p=$(cat "$work/path")
printf '%s\\n' "$3" >>"$work/calls"
if [ ! -e "$work/started" ]; then
  : >"$work/started"
  i=0
  while [ ! -e "$work/release" ] && [ "$i" -lt 500 ]; do
    sleep 0.01
    i=$((i + 1))
  done
fi
printf 'podman exec --user sas --env PATH=%s %s\\n' "$p" "$3"
"""


def _fake_distrobox(directory: Path) -> Path:
    path = directory / "distrobox"
    _ = path.write_text(_FAKE, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return path


def _wait_for(path: Path) -> None:
    deadline = time.monotonic() + _DEADLINE_S
    while not path.exists():
        if time.monotonic() > deadline:
            message = f"{path.name} never appeared"
            raise AssertionError(message)
        time.sleep(_POLL_S)


def test_w5_stale_probe_does_not_store_its_path_under_the_new_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    work = tmp_path / "w5"
    work.mkdir()
    monkeypatch.setenv("W5_DIR", str(work))
    binary = _fake_distrobox(tmp_path)
    ini = tmp_path / "distrobox.ini"
    _ = ini.write_text("[agent-claude]\nimage = one\n", encoding="utf-8")
    _ = (work / "path").write_text("/old/bin:/usr/bin\n", encoding="utf-8")
    cache = DistroboxEnvCache(ini, binary)

    first: list[str] = []

    def stale_probe() -> None:
        first.append(cache.path_for(_CONTAINER))

    worker = threading.Thread(target=stale_probe)
    worker.start()
    _wait_for(work / "started")

    # The ini changes while the first probe is blocked in the fake binary, and so does the PATH it would print.
    _ = ini.write_text("[agent-claude]\nimage = two\n", encoding="utf-8")
    _ = (work / "path").write_text("/new/bin:/usr/bin\n", encoding="utf-8")
    assert cache.path_for(_CONTAINER) == "/new/bin:/usr/bin"

    _ = (work / "release").touch()
    worker.join(timeout=_DEADLINE_S)
    assert not worker.is_alive()
    # The call that started under the old ini still gets the answer of its own probe.
    assert first == ["/old/bin:/usr/bin"]

    # The stale answer must not have overwritten the new digest's entry: the next call is a cache hit.
    assert cache.path_for(_CONTAINER) == "/new/bin:/usr/bin"
    assert (work / "calls").read_text(encoding="utf-8").splitlines() == [_CONTAINER, _CONTAINER]
