"""Snapshot installed tools into a local OCI image, without network or package installs."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path


def stage(source: Path, root: Path) -> None:
    destination = root / source.relative_to("/")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    else:
        shutil.copy2(source.resolve(), destination)


def stage_libraries(binary: Path, root: Path) -> None:
    result = subprocess.run(["ldd", str(binary)], check=True, text=True, capture_output=True)
    # The pinned Debian base supplies glibc. Other runtime libraries come from
    # the installed tools' environment, not an apt/pip operation during the build.
    system = ("libc.", "libm.", "libdl.", "libpthread.", "librt.", "libutil.", "ld-linux")
    for value in re.findall(r"=> (/\S+)", result.stdout):
        path = Path(value)
        if not path.name.startswith(system):
            target = root / path.relative_to("/")
            if not target.exists():
                stage(path, root)


def prepare(context: Path, toolchains: Path, codex: Path, base: str) -> dict[str, object]:
    root = context / "rootfs"
    root.mkdir(parents=True)
    packages: dict[str, dict[str, str]] = {}
    for name in ("core", "ui"):
        environment = toolchains / f".venv-{name}"
        stage(environment, root)
        interpreter = (environment / "bin/python").resolve()
        runtime = interpreter.parent.parent
        stage(runtime, root)
        # pyvenv.cfg and interpreter symlinks can reference the uv alias as well.
        for alias in runtime.parent.iterdir():
            if alias.is_symlink() and alias.resolve() == runtime:
                destination = root / alias.relative_to("/")
                destination.symlink_to(runtime)
        library = next((environment / "lib").glob("python*/site-packages"))
        packages[name] = {distribution.metadata["Name"]: distribution.version
                          for distribution in importlib.metadata.distributions(path=[str(library)])}
    executable = root / "opt/aconsole/bin/codex"
    executable.parent.mkdir(parents=True)
    shutil.copy2(codex, executable)
    # CLI --version does not exercise code-mode execution. The sibling host is
    # required for real tool calls and must be snapshotted with the CLI binary.
    shutil.copy2(codex.with_name("codex-code-mode-host"), executable.with_name("codex-code-mode-host"))
    for name in ("core", "ui"):
        (root / f"opt/aconsole/{name}").symlink_to(toolchains / f".venv-{name}")
    (root / "usr/bin").mkdir(parents=True, exist_ok=True)
    (root / "usr/bin/python3").symlink_to("/opt/aconsole/core/bin/python")
    stage(Path("/usr/bin/git"), root)
    stage_libraries(Path("/usr/bin/git"), root)
    node = next((toolchains / ".venv-core/lib").glob("python*/site-packages/nodejs_wheel/bin/node"))
    stage_libraries(node, root)
    stage(Path("/etc/ssl/certs/ca-certificates.crt"), root)
    (root / "etc/ssl/cert.pem").symlink_to("/etc/ssl/certs/ca-certificates.crt")
    digest = hashlib.sha256()
    files = 0
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            digest.update((relative + "->" + str(path.readlink())).encode())
        elif path.is_file():
            digest.update(relative.encode())
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            files += 1
    manifest: dict[str, object] = {"base_image": base, "tools_sha256": digest.hexdigest(),
        "files": files, "packages": packages, "network": "none", "source": "installed toolchain snapshot"}
    (context / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (context / "Containerfile").write_text(
        f"FROM {base}\n"
        'RUN ["/usr/sbin/useradd", "--uid", "1000", "--user-group", "--create-home", "--shell", "/bin/sh", "sas"]\n'
        "COPY --chown=1000:1000 rootfs/ /\n"
        "COPY manifest.json /opt/aconsole/manifest.json\n"
        f"LABEL aconsole.gate.schema=1 aconsole.gate.tools-sha256={digest.hexdigest()}\n"
        "ENV PATH=/opt/aconsole/bin:/opt/aconsole/core/bin:/usr/local/bin:/usr/bin:/bin\n"
        "USER sas\n"
        'CMD ["/usr/bin/sleep", "infinity"]\n'
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, required=True)
    parser.add_argument("--toolchains", type=Path, required=True)
    parser.add_argument("--codex", type=Path, required=True)
    parser.add_argument("--base-image", required=True)
    args = parser.parse_args()
    base = args.base_image.removeprefix("sha256:")
    if re.fullmatch(r"[0-9a-f]{64}", base) is None:
        parser.error("base image must be an immutable local image ID")
    context = args.context.resolve()
    if context.exists():
        parser.error("context must be new; existing artifacts are never overwritten")
    manifest = prepare(context, args.toolchains.resolve(), args.codex.resolve(), base)
    sys.stdout.write(json.dumps({"context": str(context), "tools_sha256": manifest["tools_sha256"],
                                "files": manifest["files"]}) + "\n")


if __name__ == "__main__":
    main()
