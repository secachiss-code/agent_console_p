"""SEC33 bounded byte response specification. Disposable lab only, no confinement."""

import json
import re
import subprocess
import sys
from pathlib import Path

IMPORT = Path(__file__).resolve().parents[1] / "candidate-snapshot-import"
sys.path.append(str(IMPORT))
from importer import SnapshotImporter as SnapshotImporter
from importer import digest, git, identity, manifest
from adapter import STATUS

MAX_OUTPUT = 1024 * 1024  # Stricter finite response subset; NOT streaming enforcement.
MAX_WIRE = 8 * MAX_OUTPUT
MAX_RECORDS = 4096
MAX_PATH = 4096


class ResponseError(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def require(value, code="MALFORMED_RESPONSE"):
    if not value:
        raise ResponseError(code)


def path_hex(raw):
    require(0 < len(raw) <= MAX_PATH, "PATH_LIMIT" if len(raw) > MAX_PATH else "MALFORMED_RESPONSE")
    require(b"\0" not in raw and not raw.startswith(b"/"))
    require(all(p not in (b"", b".", b"..") and p.lower() != b".git" for p in raw.split(b"/")))
    return raw.hex()


def oid(raw, width):
    require(re.fullmatch(b"[0-9a-f]{" + str(width).encode() + b"}", raw) is not None)
    return raw.decode("ascii")


def parse_status(raw, width):
    require(type(raw) is bytes and width in (40, 64))
    require(len(raw) <= MAX_OUTPUT, "OUTPUT_LIMIT")
    require(not raw or raw.endswith(b"\0"))
    tokens = raw[:-1].split(b"\0") if raw else []
    records = []
    headers = set()
    i = 0
    while i < len(tokens):
        require(len(records) < MAX_RECORDS, "RECORD_LIMIT")
        token = tokens[i]
        i += 1
        if token.startswith(b"# "):
            key, sep, value = token[2:].partition(b" ")
            require(sep and key in (b"branch.oid", b"branch.head", b"branch.upstream", b"branch.ab"))
            require(key not in headers and value)
            headers.add(key)
            if key == b"branch.oid" and value != b"(initial)":
                oid(value, width)
            if key == b"branch.ab":
                require(re.fullmatch(rb"\+[0-9]+ -[0-9]+", value) is not None)
            records.append(dict(kind="header", name=key.decode(), value_hex=value.hex()))
            continue
        kind = token[:1]
        if kind in (b"?", b"!"):
            require(token[1:2] == b" ")
            records.append(dict(kind="untracked" if kind == b"?" else "ignored", path_hex=path_hex(token[2:])))
            continue
        require(kind in (b"1", b"2", b"u"))
        count = {b"1": 8, b"2": 9, b"u": 10}[kind]
        fields = token.split(b" ", count)
        require(len(fields) == count + 1)
        tag, xy, sub = fields[:3]
        require(tag == kind and re.fullmatch(rb"[.MADRCUT]{2}", xy) is not None)
        require(sub == b"N...", "UNSUPPORTED_RESPONSE")
        modes = fields[3:7] if kind == b"u" else fields[3:6]
        require(all(m in (b"000000", b"100644", b"100755") for m in modes), "UNSUPPORTED_RESPONSE")
        hashes = fields[7:10] if kind == b"u" else fields[6:8]
        hashes = [oid(h, width) for h in hashes]
        row = dict(
            kind={b"1": "ordinary", b"2": "rename", b"u": "unmerged"}[kind],
            xy=xy.decode(),
            submodule=sub.decode(),
            modes=[m.decode() for m in modes],
            oids=hashes,
            path_hex=path_hex(fields[-1]),
        )
        if kind == b"2":
            score = fields[8]
            require(re.fullmatch(rb"[RC][0-9]{1,3}", score) is not None and int(score[1:]) <= 100)
            require(i < len(tokens))
            row.update(score=score.decode(), original_path_hex=path_hex(tokens[i]))
            i += 1
        records.append(row)
    require({b"branch.oid", b"branch.head"} <= headers)
    return records


def encode_status(records):
    """Exact reconstruction is checked against native bytes, including rename source."""
    tokens = []
    for row in records:
        kind = row["kind"]
        if kind == "header":
            tokens.append(b"# " + row["name"].encode() + b" " + bytes.fromhex(row["value_hex"]))
        elif kind in ("untracked", "ignored"):
            tokens.append((b"? " if kind == "untracked" else b"! ") + bytes.fromhex(row["path_hex"]))
        else:
            tag = {"ordinary": "1", "rename": "2", "unmerged": "u"}[kind]
            fields = [tag, row["xy"], row["submodule"], *row["modes"], *row["oids"]]
            if kind == "rename":
                fields.append(row["score"])
            tokens.append(" ".join(fields).encode() + b" " + bytes.fromhex(row["path_hex"]))
            if kind == "rename":
                tokens.append(bytes.fromhex(row["original_path_hex"]))
    return b"\0".join(tokens) + (b"\0" if tokens else b"")


def changed_paths(records):
    """All paths staged relative to HEAD; rename includes old and new literal bytes."""
    paths = set()
    for row in records:
        if row["kind"] in ("ordinary", "rename", "unmerged") and row["xy"][0] != ".":
            paths.add(row["path_hex"])
            if "original_path_hex" in row:
                paths.add(row["original_path_hex"])
    return sorted(paths)


def index_map(raw, width):
    require(len(raw) <= MAX_OUTPUT, "OUTPUT_LIMIT")
    require(not raw or raw.endswith(b"\0"))
    rows = {}
    for token in raw[:-1].split(b"\0") if raw else []:
        require(len(rows) < MAX_RECORDS, "RECORD_LIMIT")
        metadata, sep, path = token.partition(b"\t")
        require(sep)
        fields = metadata.split(b" ")
        require(len(fields) == 3 and fields[0] in (b"100644", b"100755") and fields[2] == b"0", "UNSUPPORTED_RESPONSE")
        key = path_hex(path)
        require(key not in rows)
        rows[key] = [fields[0].decode(), oid(fields[1], width)]
    return rows


class TypedLab:
    """Fixed diagnostic observations supplement SEC30 templates in this lab only.

    Reuses SEC31 dependency preflight. No public argv/env/runner input. Capture is
    unbounded subprocess capture BEFORE parser limits; not runtime enforcement.
    """

    def __init__(self, importer, width, after_add=None):
        self.importer = importer
        self.width = width
        self.after_add = after_add  # trusted fixture fault injection, never request input
        self.quarantined = set()
        self.commands = []

    def execute(self, request):
        # Finite wire envelope check, after capture/allocation; not a memory limit.
        try:
            request_bytes = json.dumps(request, ensure_ascii=True).encode()
        except TypeError, ValueError, RecursionError:
            return dict(ok=False, diagnostic="UNSUPPORTED_OPERATION", phase="preflight", retry_automatically=False)
        if len(request_bytes) > MAX_OUTPUT:
            return dict(ok=False, diagnostic="UNSUPPORTED_OPERATION", phase="preflight", retry_automatically=False)
        result = self._execute(request)
        if len(json.dumps(result, ensure_ascii=True).encode()) <= MAX_WIRE:
            return result
        cid = request["candidate_id"]
        self.quarantined.add(cid)
        return dict(
            ok=False,
            diagnostic="OUTPUT_LIMIT",
            phase="response",
            retry_automatically=False,
            execution_started=request["operation"] == "StageCandidatePaths",
            candidate_digest=result.get("candidate_digest"),
            index_digest=result.get("index_digest"),
            disposition="quarantine",
            observations_omitted=True,
            recovery_required=True,
        )

    def _execute(self, request):
        def deny(code, phase="preflight", **extra):
            return dict(ok=False, diagnostic=code, phase=phase, retry_automatically=False, **extra)

        if not isinstance(request, dict):
            return deny("UNSUPPORTED_OPERATION")
        op = request.get("operation")
        fields = {"operation", "candidate_id", "expected_digest"} | (
            {"paths"} if op == "StageCandidatePaths" else set()
        )
        if op not in ("InspectCandidate", "StageCandidatePaths") or set(request) != fields:
            return deny("UNSUPPORTED_OPERATION")
        if op == "StageCandidatePaths":
            paths = request["paths"]
            if (
                not isinstance(paths, list)
                or not paths
                or len(paths) > MAX_RECORDS
                or any(type(p) is not str for p in paths)
            ):
                return deny("UNSUPPORTED_OPERATION")
            try:
                require(len(set(paths)) == len(paths))
                for p in paths:
                    path_hex(p.encode("utf-8"))
                require(sum(len(p.encode()) for p in paths) <= MAX_OUTPUT)
            except ResponseError, UnicodeError:
                return deny("UNSUPPORTED_OPERATION")
        cid = request["candidate_id"]
        if not isinstance(cid, str):
            return deny("UNSUPPORTED_ORIGIN")
        if cid in self.quarantined:
            return deny("CANDIDATE_QUARANTINED")
        inspected = self.importer.adapter.execute(
            {k: request[k] for k in ("candidate_id", "expected_digest")} | {"operation": "InspectCandidate"}
        )
        if not inspected["ok"]:
            return dict(inspected, phase="preflight")
        root = self.importer.candidates[cid]
        before = manifest(root)
        started = False

        def command(args):
            self.commands.append(args)
            result = git(root, args, self.importer.home, safe=True)
            if result.returncode:
                raise GitFailure(result)
            require(len(result.stdout) <= MAX_OUTPUT, "OUTPUT_LIMIT")
            return result.stdout

        try:
            records = parse_status(bytes.fromhex(inspected["output_hex"]), self.width)
            if op == "InspectCandidate":
                return dict(
                    ok=True,
                    operation=op,
                    status=records,
                    candidate_digest=identity(root),
                    index_digest=inspected["index_digest"],
                )
            before_index = index_map(command(["ls-files", "--stage", "-z"]), self.width)
            started = True
            command(["--literal-pathspecs", "add", "-A", "--", *request["paths"]])
            if self.after_add:
                self.after_add(root)
            tree_raw = command(["write-tree"])
            require(tree_raw.endswith(b"\n") and len(tree_raw) == self.width + 1)
            tree = oid(tree_raw[:-1], self.width)
            records = parse_status(command(STATUS), self.width)
            after_index = index_map(command(["ls-files", "--stage", "-z"]), self.width)
            return dict(
                ok=True,
                operation=op,
                status=records,
                changed_paths_hex=changed_paths(records),
                index_changed_paths_hex=sorted(
                    p for p in before_index.keys() | after_index.keys() if before_index.get(p) != after_index.get(p)
                ),
                tree_oid=tree,
                index_digest=digest((root / ".git/index").read_bytes()),
                candidate_digest=identity(root),
            )
        except (ResponseError, GitFailure, subprocess.TimeoutExpired) as error:
            self.quarantined.add(cid)
            after = manifest(root)
            stderr = error.result.stderr if isinstance(error, GitFailure) else b""
            return deny(
                error.code if isinstance(error, ResponseError) else "EXECUTION_FAILED",
                "execution" if started else "response",
                execution_started=started,
                disposition="quarantine",
                recovery_required=isinstance(error, subprocess.TimeoutExpired),
                exit=error.result.returncode if isinstance(error, GitFailure) else None,
                stderr_hex=stderr[:4096].hex(),
                stderr_truncated=len(stderr) > 4096,
                candidate_digest=identity(root),
                index_digest=digest((root / ".git/index").read_bytes()) if (root / ".git/index").exists() else None,
                changed_entries=sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p)),
                before_manifest=before,
                after_manifest=after,
            )


class GitFailure(Exception):
    def __init__(self, result):
        super().__init__(result.returncode)
        self.result = result
