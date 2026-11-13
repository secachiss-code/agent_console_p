"""Install the successfully verified QA34 package at the user's explicit request."""

# ruff: noqa: INP001, S603, S101, T201, PLR0915
# Explicit bounded host update with retained logs and exact installed source verification.
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    qa = json.loads((ROOT / "artifacts/qa34/results.json").read_text())
    assert qa.get("checks_passed")
    assert not qa.get("error")
    subprocess.run([sys.executable, str(ROOT / "artifacts/qa34/verify.py")], check=True)
    package = ROOT / qa["package"]["path"]
    assert digest(package) == qa["package"]["sha256"]
    record = OUT / "results.json"
    with record.open("x") as stream:
        stream.write("{}\n")
    data = {
        "package_sha256": digest(package),
        "source_head": qa["head"],
        "runs": {},
        "runner_sha256": digest(Path(__file__)),
    }

    def save() -> None:
        record.write_text(json.dumps(data, indent=2) + "\n")

    def run(name: str, argv: list[str], allowed: tuple[int, ...] = (0,), timeout: int = 120) -> str:
        start = time.time_ns()
        # Workflow checks deliberately run outside the checkout, from /tmp.
        result = subprocess.run(argv, cwd="/tmp", capture_output=True, timeout=timeout, check=False)  # noqa: S108
        log = OUT / (name + ".log")
        log.write_bytes(result.stdout + result.stderr)
        data["runs"][name] = {
            "argv": argv,
            "exit": result.returncode,
            "start_ns": start,
            "end_ns": time.time_ns(),
            "log_sha256": digest(log),
        }
        save()
        print(name, result.returncode, flush=True)
        if result.returncode not in allowed:
            message = f"{name}: {result.returncode}; see {log}"
            raise RuntimeError(message)
        return (result.stdout + result.stderr).decode(errors="replace")

    try:
        run("processes-before", ["/usr/bin/ps", "-eo", "pid,ppid,args"])
        run(
            "status-before",
            ["/usr/bin/dpkg-query", "-W", "-f=${Status} ${Version} ${Architecture}\n", "aconsole"],
            (0, 1),
        )
        run("install", ["/usr/bin/pkexec", "/usr/bin/dpkg", "--install", str(package)], timeout=600)
        status = run(
            "status-after", ["/usr/bin/dpkg-query", "-W", "-f=${Status} ${Version} ${Architecture}\n", "aconsole"]
        )
        assert "install ok installed 0.1.1 amd64" in status
        installed = {}
        for profile in ("core", "ui"):
            sites = list(Path("/opt/aconsole", profile, "lib").glob("python3.*/site-packages"))
            assert len(sites) == 1
            matched = {}
            for name, expected in qa["source_before"].items():
                if name.startswith("src/aconsole/"):
                    target = sites[0] / name.removeprefix("src/")
                elif name.startswith("migrations/"):
                    target = sites[0] / "aconsole/_migrations" / name.removeprefix("migrations/")
                else:
                    continue
                actual = digest(target)
                assert actual == expected, str(target)
                matched[name] = actual
            installed[profile] = matched
        data["installed_hashes"] = installed
        clean = ["/usr/bin/env", "-i", "HOME=/tmp", "PATH=/usr/bin:/bin", "LANG=C.UTF-8"]
        run("ui-launcher", [*clean, "/usr/bin/aconsole", "--help"])
        run("core-launcher", [*clean, "/usr/bin/aconsoled", "--help"], (2,))
        for profile, condition in (("core", "not "), ("ui", "")):
            run(
                profile + "-import",
                [
                    *clean,
                    f"/opt/aconsole/{profile}/bin/python",
                    "-c",
                    (f"import sys; assert {condition}sys._is_gil_enabled(); "
                     f"import aconsole.{profile}; print(sys.executable)"),
                ],
            )
        run("processes-after", ["/usr/bin/ps", "-eo", "pid,ppid,args"])
        data["installed_verified"] = True
    except BaseException as error:
        data["error"] = repr(error)
        raise
    finally:
        save()


if __name__ == "__main__":
    main()
