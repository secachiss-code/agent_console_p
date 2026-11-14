# ruff: noqa: C901, EM101, INP001, PLR2004, S603, S607, T201, TRY003
# Standalone diagnostic checks retained evidence; it never establishes trust.
"""Check retained transport evidence, without issuing a trusted gate verdict."""

from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


def verify(archive: Path | None = None) -> None:
    index = json.loads((HERE / "evidence.json").read_text())
    for name, digest in index["files"].items():
        if hashlib.sha256((HERE / name).read_bytes()).hexdigest() != digest:
            raise ValueError(f"Evidence drift: {name}")  # noqa: EM102
    candidate = json.loads((HERE / "candidate.json").read_text())
    if candidate["acceptance"] != "UNVERIFIED":
        raise ValueError("Transport must not issue acceptance")
    tracked = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--", *candidate["scope"]],
        capture_output=True, check=True, timeout=30,
    ).stdout
    if {name.decode() for name in tracked.split(b"\0") if name} != set(candidate["files"]):
        raise ValueError("Source file set drift")
    for name, expected in candidate["files"].items():
        path = ROOT / name
        mode = path.lstat().st_mode
        actual_mode = "100755" if mode & stat.S_IXUSR else "100644"
        if (not stat.S_ISREG(mode) or actual_mode != expected["git_mode"]
                or hashlib.sha256(path.read_bytes()).hexdigest() != expected["sha256"]):
            raise ValueError(f"Source drift: {name}")  # noqa: EM102
    suites = ET.parse(HERE / "hostrun.xml").getroot().findall("testsuite")  # noqa: S314
    if (len(suites) != 1 or any(suites[0].get(key) != value for key, value in
                              {"tests": "219", "skipped": "1", "failures": "0", "errors": "0"}.items())):
        raise ValueError("Unexpected hostrun regression result")
    if archive is not None:
        if hashlib.sha256(archive.read_bytes()).hexdigest() != candidate["archive_sha256"]:
            raise ValueError("Archive digest mismatch")
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            if len(names) != len(set(names)) or set(names) != {"source/" + p for p in candidate["files"]}:
                raise ValueError("Archive file set mismatch")
            for name, expected in candidate["files"].items():
                info = bundle.getinfo("source/" + name)
                if (info.external_attr >> 16 != int(expected["git_mode"], 8)
                        or hashlib.sha256(bundle.read(info)).hexdigest() != expected["sha256"]):
                    raise ValueError("Archive content mismatch")
    print("Evidence consistent; candidate acceptance remains UNVERIFIED.")


if __name__ == "__main__":
    verify(Path(sys.argv[1]) if len(sys.argv) == 2 else None)
