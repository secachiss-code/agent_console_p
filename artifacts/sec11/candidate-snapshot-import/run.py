"""Finite SEC32 fixture-owned bytes producer and importer controls."""

import copy
import importlib.util
import json
import platform
import stat
import tempfile
from pathlib import Path

from importer import SnapshotImporter, Rejected, canonical, digest, identity, manifest, UPSTREAM

spec = importlib.util.spec_from_file_location("sec31_run", UPSTREAM / "run.py")
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = "b94c9da9c58db8b5623f42ee79f279b01b03a276"
FILES = ("importer.py", "run.py", "verify.py", "REPORT.md")
check, must, put = previous.check, previous.must, previous.put


def bundle_fixture(repo, fmt):
    """Only trusted, quiescent disposable fixtures; never called on live projects."""
    rows = []
    for name, row in sorted(manifest(repo).items()):
        path = repo / name
        check(row[1] in ("file", "directory"), "fixture link/special")
        item = {"path": name, "mode": row[0], "kind": row[1]}
        if row[1] == "file":
            check(path.stat().st_nlink == 1, "fixture hardlink")
            payload = path.read_bytes()
            item.update(hex=payload.hex(), sha256=digest(payload))
        rows.append(item)
    return {"schema": 1, "format": fmt, "root_mode": stat.S_IMODE(repo.stat().st_mode), "entries": rows}


def set_file(data, path, payload, mode=0o644):
    rows = data["entries"]
    for parent in reversed(list(Path(path).parents)[:-1]):
        name = parent.as_posix()
        if not any(r["path"] == name for r in rows):
            rows.append({"path": name, "kind": "directory", "mode": 0o755})
    rows[:] = [r for r in rows if r["path"] != path]
    rows.append({"path": path, "kind": "file", "mode": mode, "hex": payload.hex(), "sha256": digest(payload)})


def entry(data, path):
    return next(r for r in data["entries"] if r["path"] == path)


def run():
    cases = []
    with tempfile.TemporaryDirectory(prefix="sec32-lab-") as scratch:
        work = Path(scratch)
        home = work / "home"
        home.mkdir()
        parent = work / "candidates"
        parent.mkdir()
        importer = SnapshotImporter(parent, home)
        for fmt in ("sha1", "sha256"):
            for origin in ("detached", "unborn"):
                source = work / f"source-{fmt}-{origin}"
                previous.fixture(source, fmt, origin, home)
                put(source, ".gitattributes", "*.txt -text\n")
                put(source, ".git/info/attributes", "# retained metadata\n")
                put(source, ".git/info/exclude", "excluded-local\n")
                put(source, ".git/hooks/pre-commit", "#!/bin/sh\nexit 77\n")
                (source / "-leading").chmod(0o755)
                (source / ".git/info").chmod(0o700)
                if origin == "unborn":
                    source.chmod(0o700)
                data = bundle_fixture(source, fmt)
                raw = canonical(data)
                original = identity(source)
                stem = fmt + "-" + origin
                imported = importer.import_bytes(stem, raw, digest(raw))
                candidate = importer.candidates[stem]
                native_id = stem + "-native"
                importer.import_bytes(native_id, raw, digest(raw))
                native = importer.candidates[native_id]
                before = previous.state(candidate, home)
                check(before["digest"] == original, "roundtrip manifest")
                inspected = importer.adapter.execute(
                    previous.request(candidate, "InspectCandidate") | {"candidate_id": stem}
                )
                native_status = must(native, previous.STATUS, home)
                check(inspected["ok"] and inspected["output_hex"] == native_status.hex(), "import status parity")
                must(native, ["--literal-pathspecs", "add", "-A", "--", *previous.PATHS], home)
                native_tree = must(native, ["write-tree"], home)
                staged = importer.adapter.execute(previous.request(candidate) | {"candidate_id": stem})
                after = previous.state(candidate, home)
                native_after = previous.state(native, home)
                check(staged["ok"] and staged["output_hex"] == native_tree.hex(), "import stage parity")
                check(after["index_hex"] == native_after["index_hex"], "import index parity")
                check(
                    all(before[k] == after[k] for k in ("files", "refs_hex", "head_hex")), "stage files/refs mutation"
                )
                check(identity(source) == original, "source mutation")
                cases.append(
                    dict(
                        group="roundtrip",
                        name=stem,
                        format=fmt,
                        origin=origin,
                        imported=imported,
                        bundle_sha256=digest(raw),
                        root_mode=data["root_mode"],
                        manifest=manifest(source),
                        before=before,
                        after=after,
                        native_after=native_after,
                        inspect=inspected,
                        stage=staged,
                        native_status_hex=native_status.hex(),
                        native_tree_hex=native_tree.hex(),
                        source_before=original,
                        source_after=identity(source),
                    )
                )
                protected = identity(candidate)

                def reject(name, mutate=None, raw_edit=None, stale=False, interrupt=None, agree_edited=False):
                    changed = copy.deepcopy(data)
                    if mutate:
                        mutate(changed)
                    payload = canonical(changed)
                    agreed = digest(payload)
                    if raw_edit:
                        payload = raw_edit(payload)
                    if agree_edited:
                        agreed = digest(payload)
                    if stale:
                        agreed = digest(raw)
                    payload_before = digest(payload)
                    target_id = stem + "-bad"
                    registered = set(importer.candidates)
                    directories = sorted(p.name for p in parent.iterdir())
                    try:
                        importer.import_bytes(target_id, payload, agreed, interrupt_after=interrupt)
                    except Rejected as error:
                        diagnostic = str(error)
                    else:
                        raise AssertionError("accepted invalid bundle " + name)
                    check(set(importer.candidates) == registered, "failed import registered")
                    check(sorted(p.name for p in parent.iterdir()) == directories, "partial import retained")
                    check(
                        identity(candidate) == protected and identity(source) == original,
                        "other candidate/source mutated",
                    )
                    check(digest(payload) == payload_before, "bundle mutated")
                    cases.append(
                        dict(
                            group="reject",
                            name=name,
                            format=fmt,
                            origin=origin,
                            diagnostic=diagnostic,
                            bundle_before=payload_before,
                            bundle_after=digest(payload),
                            protected_before=protected,
                            protected_after=identity(candidate),
                            source_before=original,
                            source_after=identity(source),
                            registered_before=sorted(registered),
                            registered_after=sorted(importer.candidates),
                            directories_before=directories,
                            directories_after=sorted(p.name for p in parent.iterdir()),
                        )
                    )

                for path in (
                    "/tmp/outside",
                    "../outside",
                    "a/../../outside",
                    "a//b",
                    "./x",
                    "",
                    "nul\0x",
                    "a\\b",
                    ".GiT/config",
                    "sub/.git/config",
                    "bad\ud800name",
                ):
                    reject(
                        "path-" + repr(path),
                        lambda d, p=path: d["entries"].append(
                            {"path": p, "kind": "file", "mode": 0o644, "hex": "", "sha256": digest(b"")}
                        ),
                    )
                reject("unknown-field", lambda d: d.update(extra=True))
                reject("entry-unknown-field", lambda d: entry(d, "modified.txt").update(extra=True))
                reject("invalid-root-mode", lambda d: d.update(root_mode=0o777))
                reject("boolean-schema", lambda d: d.update(schema=True))
                reject("duplicate-path", lambda d: d["entries"].append(copy.deepcopy(d["entries"][0])))
                for kind in ("symlink", "hardlink", "fifo", "socket", "device"):
                    reject("kind-" + kind, lambda d, k=kind: entry(d, "modified.txt").update(kind=k))
                reject("file-parent", lambda d: set_file(d, "modified.txt/child", b"x"))
                reject(
                    "missing-parent",
                    lambda d: set_file(d, "absent/child", b"x") or d["entries"].remove(entry(d, "absent")),
                )
                reject("missing-git", lambda d: d["entries"].remove(entry(d, ".git")))
                reject("external-gitdir", lambda d: set_file(d, ".git", b"gitdir: /elsewhere\n"))
                for path in (
                    ".git/commondir",
                    ".git/gitdir",
                    ".git/objects/info/alternates",
                    ".git/objects/info/http-alternates",
                    ".git/modules/x",
                    ".git/info/sparse-checkout",
                    ".git/sharedindex.fake",
                    ".git/unknown",
                    ".gitmodules",
                ):
                    reject("dependency-" + path, lambda d, p=path: set_file(d, p, b"/outside\n"))
                config = bytes.fromhex(entry(data, ".git/config")["hex"])
                for name, extra in {
                    "include": b"[include]\npath=/outside\n",
                    "conditional-include": b'[includeIf "gitdir:/"]\npath=/outside\n',
                    "worktree": b"[core]\nworktree=/outside\n",
                    "hooks-path": b"[core]\nhooksPath=/outside\n",
                    "unknown-config": b"[unknown]\nvalue=x\n",
                    "fsmonitor": b"[core]\nfsmonitor=/outside\n",
                    "promisor": b'[remote "x"]\npromisor=true\n',
                }.items():
                    reject(name, lambda d, e=extra: set_file(d, ".git/config", config + e))
                reject("missing-bytes", lambda d: entry(d, "modified.txt").pop("hex"))
                reject("corrupt-bytes", lambda d: entry(d, "modified.txt").update(hex="00"))
                reject("invalid-hex", lambda d: entry(d, "modified.txt").update(hex="zz"))
                reject("bad-mode", lambda d: entry(d, "modified.txt").update(mode=0o4644))
                reject(
                    "changed-mode-stale-agreement", lambda d: entry(d, "modified.txt").update(mode=0o755), stale=True
                )
                reject("changed-bytes-stale-agreement", lambda d: set_file(d, "modified.txt", b"changed"), stale=True)
                reject(
                    "missing-file-stale-agreement", lambda d: d["entries"].remove(entry(d, "modified.txt")), stale=True
                )
                reject("wrong-format", lambda d: d.update(format="sha256" if fmt == "sha1" else "sha1"))
                reject("missing-HEAD", lambda d: d["entries"].remove(entry(d, ".git/HEAD")))
                reject(
                    "dangling-HEAD", lambda d: set_file(d, ".git/HEAD", b"0" * (40 if fmt == "sha1" else 64) + b"\n")
                )
                reject("bad-index", lambda d: set_file(d, ".git/index", b"corrupt"))
                if origin == "detached":
                    reject(
                        "missing-object",
                        lambda d: d["entries"].remove(
                            next(
                                r for r in d["entries"] if r["kind"] == "file" and r["path"].startswith(".git/objects/")
                            )
                        ),
                    )
                    reject(
                        "corrupt-object",
                        lambda d: set_file(
                            d,
                            next(
                                r["path"]
                                for r in d["entries"]
                                if r["kind"] == "file" and r["path"].startswith(".git/objects/")
                            ),
                            b"corrupt",
                        ),
                    )
                reject("duplicate-json-key", raw_edit=lambda b: b.replace(b'"schema":1', b'"schema":1,"schema":1'))
                reject("truncated-json", raw_edit=lambda b: b[:-1])
                # Structural failures are tested with an agreed digest of malformed bytes as well.
                for name, payload in (
                    ("agreed-truncated-json", raw[:-1]),
                    ("agreed-duplicate-json-key", raw.replace(b'"schema":1', b'"schema":1,"schema":1')),
                ):
                    reject(name, raw_edit=lambda b, p=payload: p, agree_edited=True)
                for position in (0, len(data["entries"]) // 2, len(data["entries"]) - 1):
                    reject("interrupt-" + str(position), interrupt=position)
                # Existing candidate IDs and unregistered existing paths must never be overwritten.
                for target_id in (stem, stem + "-occupied"):
                    target = parent / target_id
                    if target_id != stem:
                        target.symlink_to(candidate, target_is_directory=True)
                    try:
                        importer.import_bytes(target_id, raw, digest(raw))
                    except Rejected as error:
                        check(str(error) == "candidate exists", "collision diagnostic")
                    else:
                        raise AssertionError("overwrite")
                    check(identity(candidate) == protected, "collision mutation")
                    cases.append(
                        dict(
                            group="collision",
                            name="registered" if target_id == stem else "symlink",
                            format=fmt,
                            origin=origin,
                            protected_before=protected,
                            protected_after=identity(candidate),
                        )
                    )
                # Clean retry after injected interruption; registration occurs only on completion.
                retry_id = stem + "-retry"
                retried = importer.import_bytes(retry_id, raw, digest(raw))
                check(retried["candidate_digest"] == original, "retry parity")
                must(
                    importer.candidates[retry_id],
                    [
                        "update-index",
                        "--add",
                        "--cacheinfo",
                        "160000," + "1" * (40 if fmt == "sha1" else 64) + ",submodule",
                    ],
                    home,
                )
                gitlink_index = (importer.candidates[retry_id] / ".git/index").read_bytes()
                reject("gitlink-index", lambda d: set_file(d, ".git/index", gitlink_index))
                cases.append(
                    dict(
                        group="retry",
                        name=stem,
                        format=fmt,
                        origin=origin,
                        expected=original,
                        actual=retried["candidate_digest"],
                    )
                )
                for name, modification, diagnostic in (
                    (
                        "required-filter",
                        lambda d: set_file(
                            d, ".git/config", config + b'[filter "demo"]\nclean=false\nsmudge=false\nrequired=true\n'
                        ),
                        "UNSUPPORTED_HELPER",
                    ),
                    (
                        "merge-helper",
                        lambda d: set_file(d, ".git/config", config + b'[merge "demo"]\ndriver=false\n'),
                        "UNSUPPORTED_HELPER",
                    ),
                    (
                        "filter-attribute",
                        lambda d: set_file(d, ".gitattributes", b"*.txt filter=demo\n"),
                        "UNSUPPORTED_HELPER",
                    ),
                    (
                        "info-attribute",
                        lambda d: set_file(d, ".git/info/attributes", b"*.txt merge=demo\n"),
                        "UNSUPPORTED_HELPER",
                    ),
                    (
                        "executable-hook",
                        lambda d: set_file(
                            d, ".git/hooks/pre-commit", b"#!/bin/sh\nprintf RAN > marker\nexit 77\n", 0o755
                        ),
                        "UNSUPPORTED_HOOK",
                    ),
                ):
                    variant = copy.deepcopy(data)
                    modification(variant)
                    payload = canonical(variant)
                    cid = stem + "-" + name
                    result = importer.import_bytes(cid, payload, digest(payload))
                    root = importer.candidates[cid]
                    check(
                        manifest(root)
                        == {
                            r["path"]: [r["mode"], r["kind"]] + ([r["sha256"]] if r["kind"] == "file" else [])
                            for r in variant["entries"]
                        },
                        "helper/hook bytes lost",
                    )
                    for operation in ("InspectCandidate", "StageCandidatePaths"):
                        command_start = len(importer.adapter.commands)
                        outcome = importer.adapter.execute(previous.request(root, operation) | {"candidate_id": cid})
                        check(
                            outcome.get("diagnostic") == diagnostic and not outcome["ok"],
                            "imported helper/hook diagnostic",
                        )
                        check(
                            identity(root) == result["candidate_digest"]
                            and len(importer.adapter.commands) == command_start,
                            "denial executed/wrote",
                        )
                        cases.append(
                            dict(
                                group="adapter-deny",
                                name=name,
                                format=fmt,
                                origin=origin,
                                operation=operation,
                                outcome=outcome,
                                before=result["candidate_digest"],
                                after=identity(root),
                                commands=[],
                            )
                        )
            cases.extend(previous.native_helpers(work, fmt, home))
    return dict(
        schema=1,
        base_commit=BASE,
        stage_A="INCOMPLETE",
        SEC11="failed",
        SEC13="failed",
        containment=False,
        security_acceptance="NOT RUN",
        production_routing=False,
        live_capture=False,
        git=previous.git(HERE, ["--version"], Path("/tmp")).stdout.decode().strip(),
        python=platform.python_version(),
        contract_sha256=digest(previous.CONTRACT.read_bytes()),
        upstream_hashes={
            n: digest((UPSTREAM / n).read_bytes()) for n in ("adapter.py", "run.py", "verify.py", "results.json")
        },
        source_hashes={str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sorted((ROOT / "src").rglob("*.py"))},
        artifact_hashes={n: digest((HERE / n).read_bytes()) for n in FILES},
        cases=cases,
    )


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
