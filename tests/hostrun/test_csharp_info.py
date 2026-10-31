"""Tests for the fixed, read-only C# toolchain diagnostic action."""

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


_CONTEXT = "tests/fixtures/codegraph/csharp"


def _plan(repo: Path, context: str = _CONTEXT) -> actions.Plan:
    return actions.build_plan("devtools", ["csharp-info", context], repo, str(repo), 30)


def _script_argv(plan: actions.Plan) -> tuple[tuple[str, ...], str]:
    assert plan.steps[0].argv == ("podman", "start", "devtools")
    argv = plan.steps[1].argv
    assert argv[:7] == (
        "podman",
        "exec",
        "--workdir",
        argv[3],
        "devtools",
        "/bin/sh",
        "-c",
    )
    assert argv[3]
    assert len(argv) == 10
    return argv, argv[7]


def test_csharp_info_uses_one_fixed_script_and_separate_context(tmp_path: Path) -> None:
    plan = _plan(tmp_path)

    argv, script = _script_argv(plan)

    assert plan.mode == "exec"
    assert argv[8:] == ("hostrun-csharp-info", _CONTEXT)
    assert _CONTEXT not in script
    assert "$1" not in script
    assert "--list-sdks" in script
    assert "Roslyn/bincore/csc.dll" in script
    assert "-version" in script
    assert "Microsoft.NETCore.App.Ref" in script
    assert "dotnet run" not in script.lower()
    assert "dotnet restore" not in script.lower()
    assert "dotnet build" not in script.lower()
    assert "dotnet install" not in script.lower()


@pytest.mark.parametrize(
    "args",
    [
        [],
        ["csharp-info"],
        ["csharp-info", _CONTEXT, "extra"],
        ["csharp-info", "../outside"],
        ["csharp-info", "src/../outside"],
        ["csharp-info", "/etc"],
        ["csharp-info", "src;touch /tmp/injected"],
        ["csharp-info", "src$(id)"],
        ["csharp-info", "src|cat"],
        ["csharp-info", "src`id`"],
        ["csharp-info", ""],
    ],
)
def test_csharp_info_rejects_bad_context_or_argument_count(
    tmp_path: Path,
    args: list[str],
) -> None:
    with pytest.raises(actions.ActionError) as raised:
        _ = actions.build_plan("devtools", args, tmp_path, str(tmp_path), 30)

    assert raised.value.status == 400


def _write_executable(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def _fake_toolchain(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    home = tmp_path / "home"
    dotnet_root = home / ".dotnet"
    sdk = dotnet_root / "sdk" / "10.0.100"
    compiler = sdk / "Roslyn" / "bincore" / "csc.dll"
    reference_pack = dotnet_root / "packs" / "Microsoft.NETCore.App.Ref" / "10.0.0" / "ref" / "net10.0"
    fake_bin = tmp_path / "fake-bin"
    invocation_log = tmp_path / "dotnet-invocations.txt"
    fail_probe = tmp_path / "fail-list-sdks"
    compiler.parent.mkdir(parents=True)
    compiler.write_bytes(b"fake csc marker; never an executable assembly")
    reference_pack.mkdir(parents=True)
    dotnet = fake_bin / "dotnet"
    _write_executable(
        dotnet,
        "#!/bin/sh\n"
        "printf '<%s>\\n' \"$@\" >> \"$FAKE_DOTNET_LOG\"\n"
        "if [ \"${1-}\" = '--list-sdks' ] && [ \"$#\" -eq 1 ]; then\n"
        "  if [ -f \"$FAKE_DOTNET_FAIL_PROBE\" ]; then exit 45; fi\n"
        "  printf '10.0.100 [%s/sdk]\\n' \"$DOTNET_ROOT\"\n"
        "  exit 0\n"
        "fi\n"
        "if [ \"${1-}\" = \"$DOTNET_ROOT/sdk/10.0.100/Roslyn/bincore/csc.dll\" ] "
        "&& [ \"${2-}\" = '-version' ] && [ \"$#\" -eq 2 ]; then\n"
        "  if [ -f \"$FAKE_DOTNET_FAIL_CSC\" ]; then exit 46; fi\n"
        "  printf '5.9.0\\n'\n"
        "  exit 0\n"
        "fi\n"
        "exit 97\n",
    )
    return home, dotnet_root, fake_bin, invocation_log


def _run_fixed_script(
    plan: actions.Plan,
    *,
    home: Path,
    dotnet_root: Path,
    fake_bin: Path,
    invocation_log: Path,
    fail_probe: Path | None = None,
    fail_compiler: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    argv, _ = _script_argv(plan)
    shell_index = argv.index("/bin/sh")
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "DOTNET_ROOT": str(dotnet_root),
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "FAKE_DOTNET_LOG": str(invocation_log),
            "FAKE_DOTNET_FAIL_PROBE": str(fail_probe or home / "no-fail-marker"),
            "FAKE_DOTNET_FAIL_CSC": str(fail_compiler or home / "no-fail-csc-marker"),
        }
    )
    env.update(dict(plan.steps[1].env))
    env.pop("RUSTUP_HOME", None)
    return subprocess.run(
        argv[shell_index:],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=5,
    )


def test_csharp_info_reports_only_fake_installed_sdk_compiler_and_reference_paths(
    tmp_path: Path,
) -> None:
    home, dotnet_root, fake_bin, invocation_log = _fake_toolchain(tmp_path)
    plan = _plan(tmp_path)

    completed = _run_fixed_script(
        plan,
        home=home,
        dotnet_root=dotnet_root,
        fake_bin=fake_bin,
        invocation_log=invocation_log,
    )

    compiler = dotnet_root / "sdk" / "10.0.100" / "Roslyn" / "bincore" / "csc.dll"
    reference_pack = dotnet_root / "packs" / "Microsoft.NETCore.App.Ref" / "10.0.0"
    assert completed.returncode == 0, completed.stderr
    assert f"HOME={home}" in completed.stdout
    assert f"DOTNET_ROOT={dotnet_root}" in completed.stdout
    assert f"DOTNET={fake_bin / 'dotnet'}" in completed.stdout
    assert "SDK=10.0.100" in completed.stdout
    assert f"CSC={compiler}" in completed.stdout
    assert "CSC_VERSION=5.9.0" in completed.stdout
    assert f"REF={reference_pack}" in completed.stdout
    assert invocation_log.read_text(encoding="utf-8").splitlines() == [
        "<--list-sdks>",
        f"<{compiler}>",
        "<-version>",
    ]


def test_csharp_info_stops_when_preferred_dotnet_probe_fails(tmp_path: Path) -> None:
    home, dotnet_root, fake_bin, invocation_log = _fake_toolchain(tmp_path)
    fail_probe = tmp_path / "fail-list-sdks"
    fail_probe.touch()
    plan = _plan(tmp_path)

    completed = _run_fixed_script(
        plan,
        home=home,
        dotnet_root=dotnet_root,
        fake_bin=fake_bin,
        invocation_log=invocation_log,
        fail_probe=fail_probe,
    )

    assert completed.returncode != 0
    assert invocation_log.read_text(encoding="utf-8").splitlines() == ["<--list-sdks>"]
    assert "CSC_VERSION=" not in completed.stdout


def test_csharp_info_propagates_compiler_version_failure(tmp_path: Path) -> None:
    home, dotnet_root, fake_bin, invocation_log = _fake_toolchain(tmp_path)
    fail_compiler = home / "fail-csc-version"
    fail_compiler.touch()
    plan = _plan(tmp_path)
    completed = _run_fixed_script(
        plan,
        home=home,
        dotnet_root=dotnet_root,
        fake_bin=fake_bin,
        invocation_log=invocation_log,
        fail_compiler=fail_compiler,
    )

    compiler = dotnet_root / "sdk" / "10.0.100" / "Roslyn" / "bincore" / "csc.dll"
    assert completed.returncode != 0
    assert invocation_log.read_text(encoding="utf-8").splitlines() == [
        "<--list-sdks>",
        f"<{compiler}>",
        "<-version>",
    ]
    assert "CSC_VERSION=" not in completed.stdout
