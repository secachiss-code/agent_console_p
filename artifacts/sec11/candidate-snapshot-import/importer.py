"""Agreed-bytes laboratory importer; not a live capture or isolation boundary."""

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

UPSTREAM = Path(__file__).resolve().parents[1] / "candidate-contract-fixtures"
sys.path.append(str(UPSTREAM))
from adapter import LabAdapter, digest, git, identity, manifest

MAX_BYTES = 32 * 1024 * 1024


class Rejected(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Rejected(reason)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def path_parts(path):
    require(isinstance(path, str), "path type")
    require(not any(0xD800 <= ord(c) <= 0xDFFF for c in path), "path encoding")
    require(len(path.encode("utf-8")) <= 4096 and "\\" not in path and "\0" not in path, "path encoding")
    parts = path.split("/")
    require(all(p not in ("", ".", "..") for p in parts), "unsafe path")
    require(all(p.casefold() != ".git" for p in parts[1:]), "nested metadata")
    require(parts[0].casefold() != ".git" or parts[0] == ".git", "metadata alias")
    return parts


def metadata_path(path, kind, fmt):
    if path == ".git":
        return kind == "directory"
    if not path.startswith(".git/"):
        return path != ".gitmodules" and not path.endswith("/.gitmodules")
    rel = path[5:]
    if kind == "directory":
        return (
            rel in ("hooks", "info", "objects", "objects/info", "objects/pack", "refs", "logs", "logs/refs")
            or (rel.startswith(("refs/", "logs/refs/")))
            or re.fullmatch(r"objects/[0-9a-f]{2}", rel) is not None
        )
    if rel in (
        "HEAD",
        "COMMIT_EDITMSG",
        "config",
        "index",
        "packed-refs",
        "description",
        "info/exclude",
        "info/attributes",
        "logs/HEAD",
    ):
        return True
    if re.fullmatch(r"hooks/[^/]+", rel) or rel.startswith(("refs/", "logs/refs/")):
        return True
    length = 38 if fmt == "sha1" else 62
    return re.fullmatch(r"objects/[0-9a-f]{2}/[0-9a-f]{" + str(length) + "}", rel) is not None


def decode_bundle(raw, agreed_digest):
    require(type(raw) is bytes and len(raw) <= MAX_BYTES, "bundle bytes/size")
    require(digest(raw) == agreed_digest, "agreed digest mismatch")
    try:
        data = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise Rejected("invalid JSON") from error
    require(isinstance(data, dict) and set(data) == {"schema", "format", "root_mode", "entries"}, "bundle fields")
    require(
        type(data["schema"]) is int and data["schema"] == 1 and data["format"] in ("sha1", "sha256"), "schema/format"
    )
    require(type(data["root_mode"]) is int and data["root_mode"] in (0o700, 0o755), "root mode")
    require(isinstance(data["entries"], list) and 0 < len(data["entries"]) <= 4096, "entry limit")
    entries = {}
    for row in data["entries"]:
        require(isinstance(row, dict) and row.get("kind") in ("file", "directory"), "entry kind")
        kind = row["kind"]
        fields = {"path", "kind", "mode"} | ({"hex", "sha256"} if kind == "file" else set())
        require(set(row) == fields, "entry fields")
        path = row["path"]
        path_parts(path)
        require(path not in entries, "duplicate path")
        require(
            type(row["mode"]) is int
            and row["mode"] in ((0o700, 0o755) if kind == "directory" else (0o444, 0o600, 0o644, 0o755)),
            "entry mode",
        )
        require(metadata_path(path, kind, data["format"]), "unknown/external metadata dependency")
        if kind == "file":
            require(isinstance(row["hex"], str) and len(row["hex"]) <= 16 * 1024 * 1024, "file bytes limit")
            try:
                payload = bytes.fromhex(row["hex"])
            except ValueError as error:
                raise Rejected("invalid file bytes") from error
            require(payload.hex() == row["hex"] and digest(payload) == row["sha256"], "file digest/encoding")
        entries[path] = row
    for path in entries:
        parent = path.rpartition("/")[0]
        require(not parent or parent in entries and entries[parent]["kind"] == "directory", "missing/colliding parent")
    require(".git" in entries and entries[".git"]["kind"] == "directory", "standalone git directory required")
    require(
        all(p in entries and entries[p]["kind"] == "file" for p in (".git/HEAD", ".git/config")), "missing metadata"
    )
    return data, entries


def config_check(root, home, fmt):
    result = git(root, ["config", "--no-includes", "--null", "--list", "--file", str(root / ".git/config")], home)
    require(result.returncode == 0, "invalid configuration")
    essential = {
        b"core.repositoryformatversion",
        b"core.filemode",
        b"core.bare",
        b"core.logallrefupdates",
        b"extensions.objectformat",
    }
    # These known unsupported helper declarations are retained as inert bytes only.
    # LabAdapter must refuse them before status/stage; importer never invokes helpers.
    inert = {
        b"filter.demo.clean",
        b"filter.demo.smudge",
        b"filter.demo.required",
        b"filter.demo.process",
        b"merge.demo.driver",
    }
    seen = {}
    for record in result.stdout.split(b"\0"):
        if not record:
            continue
        key, _, value = record.partition(b"\n")
        key = key.lower()
        require(key not in seen, "duplicate config key")
        require(key in essential | inert, "unknown configuration dependency")
        seen[key] = value
    require(seen.get(b"core.bare") == b"false", "bare/external origin")
    require(seen.get(b"core.repositoryformatversion") == (b"0" if fmt == "sha1" else b"1"), "format version")
    require(seen.get(b"extensions.objectformat", b"sha1") == fmt.encode(), "object format mismatch")
    for key in (b"core.filemode", b"core.logallrefupdates"):
        require(key not in seen or seen[key] in (b"true", b"false"), "config boolean")


class SnapshotImporter:
    """Parent/home are trusted fresh lab directories, never bundle-controlled."""

    def __init__(self, parent, home):
        self.parent = parent
        self.home = home
        self.candidates = {}
        self.adapter = LabAdapter(self.candidates, home)

    def import_bytes(self, candidate_id, raw, agreed_digest, *, interrupt_after=None):
        require(isinstance(candidate_id, str) and re.fullmatch(r"[a-z0-9-]{1,64}", candidate_id), "candidate id")
        target = self.parent / candidate_id
        require(candidate_id not in self.candidates and not os.path.lexists(target), "candidate exists")
        data, entries = decode_bundle(raw, agreed_digest)
        temporary = Path(tempfile.mkdtemp(prefix=".import-", dir=self.parent))
        try:
            for count, (name, row) in enumerate(
                sorted(entries.items(), key=lambda item: (item[0].count("/"), item[0]))
            ):
                path = temporary / name
                if row["kind"] == "directory":
                    path.mkdir(mode=0o700)
                else:
                    with path.open("xb") as stream:
                        stream.write(bytes.fromhex(row["hex"]))
                    path.chmod(row["mode"])
                if interrupt_after is not None and count == interrupt_after:
                    raise Rejected("injected interrupted import")
            for name, row in entries.items():
                if row["kind"] == "directory":
                    (temporary / name).chmod(row["mode"])
            temporary.chmod(data["root_mode"])
            require(temporary.stat().st_mode & 0o7777 == data["root_mode"], "root mode mismatch")
            expected = {
                name: [row["mode"], row["kind"]] + ([row["sha256"]] if row["kind"] == "file" else [])
                for name, row in entries.items()
            }
            require(manifest(temporary) == expected, "materialized manifest mismatch")
            config_check(temporary, self.home, data["format"])
            # Object/index/ref completeness only. Safe fixed command; no helper/hook execution.
            checked = git(temporary, ["fsck", "--full", "--no-reflogs"], self.home, safe=True)
            require(checked.returncode == 0, "incomplete/corrupt objects/index/refs")
            indexed = git(temporary, ["ls-files", "--stage", "-z"], self.home, safe=True)
            require(indexed.returncode == 0, "invalid index")
            require(
                all(record[:6] in (b"100644", b"100755") for record in indexed.stdout.split(b"\0") if record),
                "unsupported index dependency",
            )
            require(manifest(temporary) == expected, "validation changed snapshot")
            require(not os.path.lexists(target), "candidate appeared")
            temporary.rename(target)
        except BaseException:
            shutil.rmtree(temporary)
            raise
        self.candidates[candidate_id] = target
        return {
            "candidate_id": candidate_id,
            "bundle_sha256": agreed_digest,
            "candidate_digest": identity(target),
            "entries": len(entries),
            "root_mode": data["root_mode"],
        }
