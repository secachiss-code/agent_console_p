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

from aconsole.core.infrastructure.codegraph.protocol import GraphEdge, decode_graph  # noqa: E402

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
    _ = path.write_text(contents, encoding="utf-8")
    path.chmod(0o755)


def _fake_toolchain(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    home = tmp_path / "home"
    dotnet_root = home / ".dotnet"
    sdk = dotnet_root / "sdk" / "10.0.100"
    compiler = sdk / "Roslyn" / "bincore" / "csc.dll"
    reference_pack = dotnet_root / "packs" / "Microsoft.NETCore.App.Ref" / "10.0.0" / "ref" / "net10.0"
    fake_bin = tmp_path / "fake-bin"
    invocation_log = tmp_path / "dotnet-invocations.txt"
    compiler.parent.mkdir(parents=True)
    _ = compiler.write_bytes(b"fake csc marker; never an executable assembly")
    reference_pack.mkdir(parents=True)
    dotnet = fake_bin / "dotnet"
    _write_executable(
        dotnet,
        r"""#!/bin/sh
printf '<%s>\n' "$@" >> "$FAKE_DOTNET_LOG"
if [ "${1-}" = '--list-sdks' ] \
    && [ "$#" -eq 1 ]; then
  if [ -f "$FAKE_DOTNET_FAIL_PROBE" ]; then exit 45; fi
  printf '10.0.100 [%s/sdk]\n' "$DOTNET_ROOT"
  exit 0
fi
if [ "${1-}" = "$DOTNET_ROOT/sdk/10.0.100/Roslyn/bincore/csc.dll" ] \
    && [ "${2-}" = '-version' ] \
    && [ "$#" -eq 2 ]; then
  if [ -f "$FAKE_DOTNET_FAIL_CSC" ]; then exit 46; fi
  printf '5.9.0\n'
  exit 0
fi
exit 97
""",
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
    _ = env.pop("RUSTUP_HOME", None)
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


def test_csharp_hot_path_is_published_dll_without_build(tmp_path: Path) -> None:
    plan = actions.build_plan("devtools", ["csharp", "src/Cs"], tmp_path, str(tmp_path), 30)
    argv = plan.steps[1].argv
    assert argv[5:] == (
        "dotnet",
        "exec",
        str(Path.home() / ".containers/containers/devtools/.local/lib/aconsole-csgraph/csgraph.dll"),
        "src/Cs",
    )
    assert not {"run", "build", "restore", "publish"}.intersection(argv)


@pytest.fixture(scope="module")
def published_csgraph(tmp_path_factory: pytest.TempPathFactory) -> Path:
    import shutil

    if shutil.which("dotnet") is None:
        pytest.skip("C# integration requires the devtools .NET SDK")
    project = tmp_path_factory.mktemp("csgraph-publish")
    for name in ("Program.cs", "csgraph.csproj"):
        _ = shutil.copyfile(ROOT / "tools" / "csgraph" / name, project / name)
    completed = subprocess.run(
        [
            "dotnet",
            "publish",
            str(project / "csgraph.csproj"),
            "-c",
            "Release",
            "-o",
            str(project / "published"),
            "-p:NuGetAudit=false",
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return project / "published" / "csgraph.dll"


def _csharp_fixture(root: Path) -> None:
    contracts = root / "Contracts"
    app = root / "App"
    contracts.mkdir()
    app.mkdir()
    _ = (contracts / "Contracts.csproj").write_text(
        r"""<Project Sdk="Microsoft.NET.Sdk">
<PropertyGroup>
<TargetFramework>net10.0</TargetFramework>
</PropertyGroup>
</Project>""",
        encoding="utf-8",
    )
    _ = (app / "App.csproj").write_text(
        r"""<Project Sdk="Microsoft.NET.Sdk">
<PropertyGroup>
<TargetFramework>net10.0</TargetFramework>
</PropertyGroup>
<ItemGroup>
<ProjectReference Include="../Contracts/Contracts.csproj" />
</ItemGroup>
<Target Name="MustNeverExecute" BeforeTargets="Build">
<Error Text="Project code must not execute" />
</Target>
</Project>""",
        encoding="utf-8",
    )
    _ = (contracts / "Api.cs").write_text(
        "namespace Api; public interface IService { int Read(); } public class Base {}",
        encoding="utf-8",
    )
    _ = (app / "Service.cs").write_text(
        "using Api; public class Service : Base, IService { public int Read() => 1; }",
        encoding="utf-8",
    )
    _ = (app / "Unknown.cs").write_text("public class Unknown : External {}", encoding="utf-8")
    generated = app / "obj"
    generated.mkdir()
    _ = (generated / "Ignore.cs").write_text("public class Ignore {}", encoding="utf-8")
    _ = (root / "Example.sln").write_text(
        'Project("guid") = "App", "App\\App.csproj", "app-guid"\nEndProject\n',
        encoding="utf-8",
    )


def _extract_csgraph(dll: Path, root: Path, input_path: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["dotnet", "exec", str(dll), input_path],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_published_csharp_extracts_semantic_cross_project_edges_offline_and_repeatably(
    published_csgraph: Path,
    tmp_path: Path,
) -> None:
    _csharp_fixture(tmp_path)
    first = _extract_csgraph(published_csgraph, tmp_path, "Example.sln")
    second = _extract_csgraph(published_csgraph, tmp_path, "App/App.csproj")
    assert first.returncode == second.returncode == 0, first.stderr + second.stderr
    assert first.stdout == second.stdout
    graph = decode_graph(first.stdout.encode())
    assert graph.version == 1
    assert graph.edges == (
        GraphEdge(source="cs:App/Service.cs", target="cs:Contracts/Api.cs", kind="implements"),
        GraphEdge(source="cs:App/Service.cs", target="cs:Contracts/Api.cs", kind="inherits"),
    )
    assert [node.path for node in graph.nodes] == ["App/Service.cs", "App/Unknown.cs", "Contracts/Api.cs"]
    assert all(node.kind == "module" and node.id == "cs:" + node.path for node in graph.nodes)
    signatures = next(node.symbols for node in graph.nodes if node.path == "App/Service.cs")
    assert any("Read()" in signature for signature in signatures)
    assert not any("=>" in signature for signature in signatures)
    assert not (tmp_path / "App" / "bin").exists()
    assert not (tmp_path / "App" / "obj" / "project.assets.json").exists()
    # Both invocations work after the extractor's build tree is removed.
    import shutil

    shutil.rmtree(published_csgraph.parent.parent / "obj", ignore_errors=True)
    shutil.rmtree(published_csgraph.parent.parent / "bin", ignore_errors=True)
    repeated = _extract_csgraph(published_csgraph, tmp_path, "Example.sln")
    assert repeated.returncode == 0, repeated.stderr
    assert repeated.stdout == first.stdout


def test_csharp_syntax_error_has_empty_symbols_and_no_outgoing_edges(
    published_csgraph: Path,
    tmp_path: Path,
) -> None:
    _csharp_fixture(tmp_path)
    _ = (tmp_path / "App" / "Service.cs").write_text("public class Broken : Api.IService {", encoding="utf-8")
    completed = _extract_csgraph(published_csgraph, tmp_path, "App/App.csproj")
    assert completed.returncode == 0, completed.stderr
    graph = decode_graph(completed.stdout.encode())
    assert graph.edges == ()
    assert next(node.symbols for node in graph.nodes if node.path == "App/Service.cs") == ()


def test_csharp_rejects_unsupported_compile_selection_and_project_cycles(
    published_csgraph: Path,
    tmp_path: Path,
) -> None:
    _csharp_fixture(tmp_path)
    project = tmp_path / "Contracts" / "Contracts.csproj"
    _ = project.write_text('<Project><ItemGroup><Compile Remove="Api.cs" /></ItemGroup></Project>', encoding="utf-8")
    unsupported = _extract_csgraph(published_csgraph, tmp_path, "Example.sln")
    assert unsupported.returncode != 0
    assert unsupported.stdout == ""
    assert "unsupported MSBuild evaluation" in unsupported.stderr
    _ = project.write_text(
        '<Project><ItemGroup><ProjectReference Include="../App/App.csproj" /></ItemGroup></Project>',
        encoding="utf-8",
    )
    cycle = _extract_csgraph(published_csgraph, tmp_path, "Example.sln")
    assert cycle.returncode != 0
    assert cycle.stdout == ""
    assert "cyclic project references" in cycle.stderr


def test_csharp_symbols_ignore_method_bodies_and_track_signatures(
    published_csgraph: Path,
    tmp_path: Path,
) -> None:
    _csharp_fixture(tmp_path)
    before = _extract_csgraph(published_csgraph, tmp_path, "Example.sln")
    source = tmp_path / "App" / "Service.cs"
    _ = source.write_text(
        "using Api; public class Service : Base, IService { public int Read() => 200; }", encoding="utf-8"
    )
    body = _extract_csgraph(published_csgraph, tmp_path, "Example.sln")
    assert before.returncode == body.returncode == 0
    assert before.stdout == body.stdout
    _ = source.write_text(
        "using Api; public class Service : Base, IService { public int Read(int count) => count; }", encoding="utf-8"
    )
    signature = _extract_csgraph(published_csgraph, tmp_path, "Example.sln")
    assert signature.returncode == 0
    assert decode_graph(signature.stdout.encode()).nodes != decode_graph(before.stdout.encode()).nodes


def test_csharp_rejects_source_symlinks_outside_repository(
    published_csgraph: Path,
    tmp_path: Path,
) -> None:
    _csharp_fixture(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside.cs"
    _ = outside.write_text("public class External {}", encoding="utf-8")
    (tmp_path / "App" / "Link.cs").symlink_to(outside)
    completed = _extract_csgraph(published_csgraph, tmp_path, "Example.sln")
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "symlink" in completed.stderr
