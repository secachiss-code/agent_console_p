"""Check recorded host update evidence offline."""
# ruff: noqa: INP001, S101, T201, PLR2004 -- standalone evidence verifier
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    data = json.loads((OUT / "results.json").read_text())
    qa = json.loads((ROOT / "artifacts/qa34/results.json").read_text())
    assert data["runner_sha256"] == digest(OUT / "update.py")
    assert data["package_sha256"] == qa["package"]["sha256"]
    assert data["source_head"] == qa["head"]
    end = 0
    processes = json.loads((OUT / "process-state.json").read_text())
    for name, run in data["runs"].items():
        assert end <= run["start_ns"] <= run["end_ns"]
        end = run["end_ns"]
        log = OUT / (name + ".log")
        if name.startswith("processes-"):
            # Full host process inventories stay local; publish only app PID/argv evidence.
            assert processes[name]["raw_sha256"] == run["log_sha256"]
            if log.exists():
                assert digest(log) == run["log_sha256"]
        else:
            assert digest(log) == run["log_sha256"]
    if data.get("installed_verified"):
        assert not data.get("error")
        for name in ("install", "status-after", "ui-launcher", "core-import", "ui-import"):
            assert data["runs"][name]["exit"] == 0
        assert data["runs"]["core-launcher"]["exit"] == 2
        expected = {name: value for name, value in qa["source_before"].items()
                    if name.startswith(("src/aconsole/", "migrations/"))}
        assert data["installed_hashes"]["core"] == expected
        assert data["installed_hashes"]["ui"] == expected
        print("Verified host installation and", len(expected), "source files per environment")
    else:
        assert data.get("error")
        print("Verified retained update failure:", data["error"])


if __name__ == "__main__":
    main()
