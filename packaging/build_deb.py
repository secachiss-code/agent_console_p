#!/usr/bin/env python3
"""Build `dist/aconsole_<version>_amd64.deb` from the two locked environments of this checkout.

Run `./install.sh` first: nothing is downloaded here. The package carries two relocatable Python builds under
`/opt/aconsole`, `core` (free-threaded) and `ui` (with the GIL), each with only what the app needs at run time.
GTK, libadwaita, VTE, Git and Podman stay the system's.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from pathlib import Path
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
APP_ID: Final = "io.github.aconsole.AConsole"
INSTALLED: Final = "opt/aconsole"
# Only for writing tests and stubs: never imported when the app runs.
_NOT_AT_RUN_TIME: Final = frozenset({"pygobject-stubs"})
_OTHER_PLATFORM: Final = re.compile(r"sys_platform == '(?!linux')")
_SKIP_BIN: Final = re.compile(r"(idle|pydoc|pip|.*-config)")
_SKIP_LIB: Final = re.compile(r"(libtcl|libtk|libtcl9tk|tcl|tk|itcl|thread|pkgconfig|.*\.a$)")
_SKIP_STDLIB: Final = frozenset({"test", "idlelib", "tkinter", "turtledemo", "ensurepip", "site-packages"})
_DEPENDS: Final = (
    "git",
    "podman",
    "gir1.2-gtk-4.0 (>= 4.14)",
    "gir1.2-adw-1 (>= 1.5)",
    "gir1.2-vte-3.91",
    "libgirepository-2.0-0",
    "libcairo2",
    "libcairo-gobject2",
    "libffi8",
)
# The window's and the core's own interpreters, whatever the shell's Python settings say.
_LAUNCHER: Final = """#!/bin/sh
unset PYTHONHOME PYTHONPATH
export PYTHONNOUSERSITE=1
exec /{installed}/{env}/bin/python -m {module} "$@"
"""
# Root owns /opt, so an app run by a user could never write its bytecode there.
_POSTINST: Final = """#!/bin/sh
set -e
if [ "$1" = configure ]; then
    /{installed}/core/bin/python -m compileall -q -j 0 /{installed}/core/lib >/dev/null 2>&1 || true
    /{installed}/ui/bin/python -m compileall -q -j 0 /{installed}/ui/lib >/dev/null 2>&1 || true
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
    gtk-update-icon-cache -q -t /usr/share/icons/hicolor 2>/dev/null || true
fi
"""
_PRERM: Final = """#!/bin/sh
set -e
find /{installed} -type d -name __pycache__ -prune -exec rm -rf {{}} + 2>/dev/null || true
"""


def _normal(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _closure() -> tuple[frozenset[str], frozenset[str]]:
    """What the core and the UI need, from the lock. The UI imports the shared contracts, so it needs the core's too."""
    packages = {package["name"]: package for package in tomllib.loads((ROOT / "uv.lock").read_text("utf-8"))["package"]}

    def walk(entries: list[dict[str, object]]) -> frozenset[str]:
        seen: set[str] = set()
        done: set[tuple[str, str]] = set()
        while entries:
            entry = entries.pop()
            name = str(entry["name"])
            if name in _NOT_AT_RUN_TIME or _OTHER_PLATFORM.search(str(entry.get("marker", ""))):
                continue
            package = packages[name]
            if name not in seen:
                seen.add(name)
                entries.extend(package.get("dependencies", ()))
            # `pyjwt[crypto]`: what an extra asks for is listed apart from the package's own needs.
            for extra in entry.get("extra", ()):
                if (name, extra) not in done:
                    done.add((name, extra))
                    entries.extend(package["optional-dependencies"][extra])
        return frozenset(seen)

    project = packages["aconsole"]
    core = walk(list(project["dependencies"]))
    ui = walk(list(project["dev-dependencies"]["ui"]))
    return core, core | ui


def _copy_python(venv: Path, dest: Path) -> Path:
    """The interpreter the environment was made from, without what only a developer uses. Returns its site-packages."""
    home = (venv / "bin" / "python").resolve().parents[1]
    stdlib = next((home / "lib").glob("python3.*"))
    site = dest / "lib" / stdlib.name / "site-packages"

    def skipped(folder: str, names: list[str]) -> list[str]:
        here = Path(folder)
        if here == home:
            return [name for name in names if name not in {"bin", "lib"}]
        if here == home / "bin":
            return [name for name in names if _SKIP_BIN.match(name)]
        if here == home / "lib":
            return [name for name in names if name != stdlib.name and _SKIP_LIB.match(name)]
        extra = _SKIP_STDLIB if here == stdlib else frozenset()
        return [name for name in names if name in extra or name == "__pycache__"]

    _ = shutil.copytree(home, dest, symlinks=True, ignore=skipped)
    site.mkdir()
    return site


def _copy_distributions(venv: Path, site: Path, wanted: frozenset[str]) -> None:
    """Each wanted distribution, by the files it says it installed. Fails when one is missing from the environment."""
    source = next((venv / "lib").glob("python3.*/site-packages"))
    found: set[str] = set()
    for info in source.glob("*.dist-info"):
        name = _normal(info.name.removesuffix(".dist-info").rpartition("-")[0])
        if name not in wanted:
            continue
        found.add(name)
        for line in (info / "RECORD").read_text("utf-8").splitlines():
            relative = line.split(",", 1)[0]
            if relative.startswith("..") or "__pycache__" in relative:
                continue
            target = site / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            _ = shutil.copy2(source / relative, target)
    missing = wanted - found
    if missing:
        message = f"not in {venv}: {sorted(missing)}. Run ./install.sh first."
        raise SystemExit(message)


def _copy_app(site: Path) -> None:
    """Tracked files only, so a checkout's leftovers never ship. Their times stay: the UI compares them to the core."""
    listed = subprocess.run(
        ("git", "ls-files", "src/aconsole", "migrations"),  # noqa: S607 -- Git by name
        cwd=ROOT, check=True, capture_output=True, text=True,
    ).stdout
    for line in listed.splitlines():
        source = ROOT / line
        parts = Path(line).parts[1:]
        relative = Path(*parts) if line.startswith("src/") else Path("aconsole", "_migrations", *parts)
        target = site / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copy2(source, target)


def _write(path: Path, text: str, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _ = path.write_text(text, encoding="utf-8")
    path.chmod(0o755 if executable else 0o644)


def _share(tree: Path) -> None:
    share = tree / "usr" / "share"
    icons = share / "icons" / "hicolor"
    entry = (ROOT / "packaging" / f"{APP_ID}.desktop").read_text("utf-8")
    _write(share / "applications" / f"{APP_ID}.desktop", re.sub(r"(?m)^Exec=.*$", "Exec=/usr/bin/aconsole", entry))
    _write(icons / "scalable" / "apps" / f"{APP_ID}.svg", (ROOT / "packaging" / f"{APP_ID}.svg").read_text("utf-8"))
    symbolic = (ROOT / "packaging" / f"{APP_ID}-symbolic.svg").read_text("utf-8")
    _write(icons / "symbolic" / "apps" / f"{APP_ID}-symbolic.svg", symbolic)


def _control(tree: Path, version: str) -> None:
    fields = (
        ("Package", "aconsole"),
        ("Version", version),
        ("Architecture", "amd64"),
        ("Maintainer", "AConsole <noreply@aconsole.invalid>"),
        ("Section", "devel"),
        ("Priority", "optional"),
        ("Depends", ", ".join(_DEPENDS)),
        ("Description", "Agents Console: run CLI coding agents side by side\n Carries its own Python 3.15 under /opt."),
    )
    control = tree / "DEBIAN"
    _write(control / "control", "".join(f"{key}: {value}\n" for key, value in fields))
    _write(control / "postinst", _POSTINST.format(installed=INSTALLED), executable=True)
    _write(control / "prerm", _PRERM.format(installed=INSTALLED), executable=True)


def build(out: Path) -> Path:
    version: str = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    core, ui = _closure()
    with tempfile.TemporaryDirectory(prefix="aconsole-deb-") as folder:
        tree = Path(folder) / "tree"
        for env, venv, wanted in (("core", ".venv-core", core), ("ui", ".venv-ui", ui)):
            site = _copy_python(ROOT / venv, tree / INSTALLED / env)
            _copy_distributions(ROOT / venv, site, wanted)
            _copy_app(site)
        launchers = (("aconsole", "ui", "aconsole"), ("aconsoled", "core", "aconsole.core"))
        for name, env, module in launchers:
            text = _LAUNCHER.format(installed=INSTALLED, env=env, module=module)
            _write(tree / "usr" / "bin" / name, text, executable=True)
        _share(tree)
        _control(tree, version)
        out.mkdir(parents=True, exist_ok=True)
        package = out / f"aconsole_{version}_amd64.deb"
        env = {**os.environ, "SOURCE_DATE_EPOCH": str(int((ROOT / "uv.lock").stat().st_mtime))}
        _ = subprocess.run(  # noqa: S603 -- fixed argv
            ("dpkg-deb", "--root-owner-group", "--build", str(tree), str(package)),  # noqa: S607 -- by name
            check=True, env=env, stdout=subprocess.DEVNULL,
        )
    return package


if __name__ == "__main__":
    print(build(ROOT / "dist"))  # noqa: T201 -- a build tool says what it built
