"""SEC31 executable compatibility specification. LAB ONLY, no isolation boundary.

Registration is private to the fixture builder. No importer, RPC, installed runtime,
resource enforcement or concurrent-mutation protection is provided by this module.
"""

import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

GIT = shutil.which("git")
STATUS = ["status", "--porcelain=v2", "-z", "--branch", "--untracked-files=all", "--ignore-submodules=none"]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def manifest(root):
    """Regular bytes, directory/file modes and symlink targets; never follow links."""
    rows = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in sorted(dirs + files):
            path = Path(directory) / name
            mode = path.lstat().st_mode
            rel = path.relative_to(root).as_posix()
            if stat.S_ISLNK(mode):
                value = [stat.S_IMODE(mode), "link", os.readlink(path)]
            elif stat.S_ISDIR(mode):
                value = [stat.S_IMODE(mode), "directory"]
            elif stat.S_ISREG(mode):
                value = [stat.S_IMODE(mode), "file", digest(path.read_bytes())]
            else:
                value = [stat.S_IMODE(mode), "special"]
            rows[rel] = value
    return rows


def identity(root):
    return digest(json.dumps(manifest(root), sort_keys=True).encode())


def environment(home):
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(home),
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_AUTHOR_NAME": "Fixture",
        "GIT_AUTHOR_EMAIL": "fixture@example.invalid",
        "GIT_COMMITTER_NAME": "Fixture",
        "GIT_COMMITTER_EMAIL": "fixture@example.invalid",
        "GIT_AUTHOR_DATE": "2001-01-01T00:00:00+0000",
        "GIT_COMMITTER_DATE": "2001-01-01T00:00:00+0000",
    }


def git(root, argv, home, *, safe=False):
    # Timeout/capture here are lab conveniences, NOT the proposed runtime limits.
    prefix = ["-c", "core.hooksPath=/dev/null"] if safe else []
    return subprocess.run([GIT, *prefix, *argv], cwd=root, env=environment(home), capture_output=True, timeout=15)


class LabAdapter:
    def __init__(self, candidates, home):
        self.candidates = candidates  # fixture-owned registry, never supplied in requests
        self.home = home
        self.commands = []

    def execute(self, request):
        def deny(code, detail):
            return {"ok": False, "diagnostic": code, "detail": detail, "retry_automatically": False}

        if not isinstance(request, dict) or not isinstance(request.get("operation"), str):
            return deny("UNSUPPORTED_OPERATION", "typed request required")
        operation = request["operation"]
        if operation not in ("InspectCandidate", "StageCandidatePaths"):
            return deny("UNSUPPORTED_OPERATION", "operation outside v1")
        fields = {"operation", "candidate_id", "expected_digest"}
        if operation == "StageCandidatePaths":
            fields.add("paths")
        if set(request) != fields or len(json.dumps(request).encode()) > 1048576:
            return deny("UNSUPPORTED_OPERATION", "unknown/missing fields or oversized request")
        candidate_id = request["candidate_id"]
        if not isinstance(candidate_id, str) or candidate_id not in self.candidates:
            return deny("UNSUPPORTED_ORIGIN", "unregistered candidate")
        root = self.candidates[candidate_id]
        observed = identity(root)
        if request["expected_digest"] != observed:
            return deny("INPUT_UNSTABLE", "snapshot identity differs")
        rows = manifest(root)
        if any(r[1] in ("link", "special") for r in rows.values()):
            return deny("UNSUPPORTED_ORIGIN", "lab does not capture symlink/special dependencies")
        metadata = root / ".git"
        if (
            not metadata.is_dir()
            or any(
                (metadata / p).exists()
                for p in ("commondir", "gitdir", "objects/info/alternates", "modules", "info/sparse-checkout")
            )
            or any(metadata.glob("sharedindex.*"))
            or (root / ".gitmodules").exists()
        ):
            return deny("UNSUPPORTED_ORIGIN", "standalone origin required")
        config = git(
            root, ["config", "--no-includes", "--null", "--list", "--file", str(metadata / "config")], self.home
        )
        if config.returncode:
            return deny("UNSUPPORTED_ORIGIN", "uncertain config capture")
        for record in config.stdout.split(b"\0"):
            if not record:
                continue
            key, _, value = record.partition(b"\n")
            key = key.lower()
            if key.startswith((b"include.", b"includeif.")):
                return deny("UNSUPPORTED_ORIGIN", "includes not captured by lab")
            if key == b"core.hookspath":
                return deny("UNSUPPORTED_HOOK", "custom hook path")
            if key in (
                b"core.bare",
                b"core.worktree",
                b"extensions.worktreeconfig",
                b"core.sparsecheckout",
                b"core.splitindex",
            ):
                if key != b"core.bare" or value != b"false":
                    return deny("UNSUPPORTED_ORIGIN", "unsupported repository configuration")
            allowed = {
                b"core.repositoryformatversion",
                b"core.filemode",
                b"core.bare",
                b"core.logallrefupdates",
                b"extensions.objectformat",
            }
            if key not in allowed:
                return deny(
                    "UNSUPPORTED_HELPER", "configured dependency: " + key.decode("utf-8", "backslashreplace")[:160]
                )
        hooks = metadata / "hooks"
        if hooks.exists() and any(p.is_file() and p.stat().st_mode & 0o111 for p in hooks.iterdir()):
            return deny("UNSUPPORTED_HOOK", "executable hook including samples/unknown names")
        for rel in rows:
            if rel == ".git/info/attributes" or (
                not rel.startswith(".git/") and rel.split("/")[-1] == ".gitattributes"
            ):
                tokens = (root / rel).read_bytes().split()
                if any(t.lstrip(b"-!").split(b"=")[0] in (b"filter", b"merge", b"diff") for t in tokens):
                    return deny("UNSUPPORTED_HELPER", "attribute dependency: " + rel[:160])
        paths = request.get("paths")
        if operation == "StageCandidatePaths":
            if (
                not isinstance(paths, list)
                or not paths
                or len(paths) > 4096
                or any(not isinstance(p, str) for p in paths)
                or len(set(paths)) != len(paths)
            ):
                return deny("UNSUPPORTED_OPERATION", "invalid literal paths")
            for path in paths:
                parts = path.split("/")
                if (
                    "\0" in path
                    or path.startswith("/")
                    or any(p in ("", ".", "..") for p in parts)
                    or any(p.casefold() == ".git" for p in parts)
                ):
                    return deny("UNSUPPORTED_OPERATION", "invalid literal path")
        # Recheck fixture identity before execution. This is NOT a race barrier.
        if identity(root) != observed:
            return deny("INPUT_UNSTABLE", "dependency changed during preflight")
        templates = (
            [STATUS]
            if operation == "InspectCandidate"
            else [["--literal-pathspecs", "add", "-A", "--", *paths], ["write-tree"]]
        )
        outputs = []
        for argv in templates:
            self.commands.append(argv)
            result = git(root, argv, self.home, safe=True)
            if result.returncode:
                return {
                    **deny("EXECUTION_FAILED", "candidate must be discarded; execution started"),
                    "exit": result.returncode,
                    "stderr_hex": result.stderr[:4096].hex(),
                    "candidate_digest": identity(root),
                }
            outputs.append(result.stdout)
        return {
            "ok": True,
            "output_hex": outputs[-1].hex(),
            "candidate_digest": identity(root),
            "index_digest": digest((metadata / "index").read_bytes()) if (metadata / "index").exists() else None,
        }
