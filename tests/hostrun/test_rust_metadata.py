"""Rust metadata action coverage; all Cargo execution here uses local stubs."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys_path = str(ROOT / "tools" / "hostrun")
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

from tools.hostrun import actions  # noqa: E402


def _manifest(repo: Path) -> Path:
    manifest = repo / "crates" / "demo" / "Cargo.toml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("[package]\nname = 'demo'\nversion = '0.1.0'\n", encoding="utf-8")
    return manifest


def _rust_step(repo: Path, path: str) -> actions.Step:
    plan = actions.build_plan("devtools", ["rust-metadata", path], repo, str(repo), 30)
    assert len(plan.steps) == 2
    return plan.steps[1]


@pytest.mark.parametrize("requested", ["crates/demo", "crates/demo/"])
def test_rust_metadata_normalizes_directory_to_cargo_manifest(
    tmp_path: Path,
    requested: str,
) -> None:
    manifest = _manifest(tmp_path)

    step = _rust_step(tmp_path, requested)

    assert step.argv[-1] == str(manifest.resolve())


def test_rust_metadata_accepts_manifest_filename_without_appending_another(
    tmp_path: Path,
) -> None:
    manifest = _manifest(tmp_path)

    step = _rust_step(tmp_path, "crates/demo/Cargo.toml")

    assert step.argv[-1] == str(manifest.resolve())
    assert not step.argv[-1].endswith("Cargo.toml/Cargo.toml")


@pytest.mark.parametrize(
    "requested",
    [
        "../outside/Cargo.toml",
        "crates/../outside/Cargo.toml",
        "crates/missing/Cargo.toml",
        "crates/demo/not-a-manifest.txt",
    ],
)
def test_rust_metadata_rejects_traversal_and_missing_or_wrong_manifest(
    tmp_path: Path,
    requested: str,
) -> None:
    _manifest(tmp_path)

    with pytest.raises(actions.ActionError, match="devtools path"):
        _ = _rust_step(tmp_path, requested)


def test_rust_metadata_rejects_manifest_symlink_escape(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    repo.mkdir()
    manifest = _manifest(outside)
    (repo / "escape").symlink_to(manifest.parent, target_is_directory=True)

    with pytest.raises(actions.ActionError, match="devtools path"):
        _ = _rust_step(repo, "escape/Cargo.toml")


def _write_executable(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.parametrize("use_rustup_home", [False, True])
def test_rust_metadata_script_uses_installed_toolchain_offline_and_passes_manifest_as_argv(
    tmp_path: Path,
    use_rustup_home: bool,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    manifest = _manifest(repo)
    home = tmp_path / "home"
    rustup_home = tmp_path / "rustup-home" if use_rustup_home else home / ".rustup"
    tool_bin = rustup_home / "toolchains" / "stable-x86_64-unknown-linux-gnu" / "bin"
    args_log = tmp_path / "cargo-args"
    path_log = tmp_path / "cargo-path"
    _write_executable(
        tool_bin / "cargo",
        "#!/bin/sh\nprintf '%s\\n' \"$@\" > \"$CARGO_ARGS_FILE\"\nprintf '%s' \"$PATH\" > \"$CARGO_PATH_FILE\"\n",
    )
    _write_executable(tool_bin / "rustc", "#!/bin/sh\nexit 0\n")

    step = _rust_step(repo, "crates/demo/Cargo.toml")
    argv = step.argv
    shell_index = argv.index("/bin/sh")
    assert argv[shell_index + 1] == "-c"
    script = argv[shell_index + 2]
    assert str(manifest.resolve()) not in script
    assert "--offline" in script
    assert "$1" in script
    assert "/home/sas/.containers/containers/devtools/.rustup" in script
    assert "rustup toolchain install" not in script.lower()
    assert "cargo install" not in script.lower()
    assert argv[-1] == str(manifest.resolve())

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "CARGO_ARGS_FILE": str(args_log),
            "CARGO_PATH_FILE": str(path_log),
            "PATH": "/usr/bin:/bin",
        }
    )
    if use_rustup_home:
        env["RUSTUP_HOME"] = str(rustup_home)
    else:
        env.pop("RUSTUP_HOME", None)
    completed = subprocess.run(
        argv[shell_index:],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )

    assert completed.returncode == 0, completed.stderr
    assert args_log.read_text(encoding="utf-8").splitlines() == [
        "metadata",
        "--offline",
        "--format-version",
        "1",
        "--no-deps",
        "--manifest-path",
        str(manifest.resolve()),
    ]
    assert str(tool_bin) in path_log.read_text(encoding="utf-8")
