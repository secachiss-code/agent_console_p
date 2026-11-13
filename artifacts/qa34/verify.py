"""Verify retained QA34 evidence without building, installing or contacting Podman."""

# ruff: noqa: INP001, S101, PLR2004, T201 -- standalone evidence verifier
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    data = json.loads((OUT / "results.json").read_text())
    assert data["runner_sha256"] == digest(OUT / "run.txt")
    assert data["source_before"] == data["source_after"]
    for name, expected in data["source_before"].items():
        assert digest(ROOT / name) == expected, name
    previous = 0
    for name, run in data["runs"].items():
        assert previous <= run["start_ns"] <= run["end_ns"], name
        previous = run["end_ns"]
        assert digest(OUT / (name + ".log")) == run["log_sha256"], name
    if "package" in data:
        package = ROOT / data["package"]["path"]
        assert digest(package) == data["package"]["sha256"]
        assert package.stat().st_size == data["package"]["size"]
    if data.get("checks_passed"):
        assert "error" not in data
        assert data["uid"] != 0
        for name in (
            "build",
            "pull",
            "create",
            "copy",
            "start",
            "apt-update",
            "apt-install",
            "dpkg-status",
            "maintainer-scripts",
            "dpkg-log",
            "ui-launcher",
            "core-import",
            "ui-import",
            "cleanup",
        ):
            assert data["runs"][name]["exit"] == 0, name
        assert data["runs"]["core-launcher"]["exit"] == 2
        assert data["runs"]["cleanup-absent"]["exit"] == 1
        assert data["image_digest"].startswith("sha256:")
        inspect = json.loads((OUT / "container-before.log").read_text())[0]
        assert not inspect["Mounts"]
        assert not inspect["HostConfig"]["Binds"]
        assert inspect["Image"] == data["image_id"]
        assert (OUT / "copied-hash.log").read_text().split()[0] == data["package"]["sha256"]
        assert "install ok installed 0.1.1 amd64" in (OUT / "dpkg-status.log").read_text()
        assert "aconsole.postinst" in (OUT / "apt-install.log").read_text()
        print("QA34 retained successful build/install evidence verified")
    else:
        assert data.get("error")
        print("QA34 retained failure evidence verified:", data["error"])


if __name__ == "__main__":
    main()
