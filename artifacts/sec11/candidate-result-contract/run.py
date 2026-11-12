"""SEC33 finite native controls on imported disposable byte bundles."""

import importlib.util
import json
import platform
import tempfile

import model
from pathlib import Path

from model import IMPORT, MAX_OUTPUT, MAX_PATH, MAX_RECORDS, ResponseError, SnapshotImporter, TypedLab
from model import STATUS, digest, encode_status, git, identity, index_map, parse_status

spec = importlib.util.spec_from_file_location("sec32_run", IMPORT / "run.py")
upstream = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upstream)
previous = upstream.previous
HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
BASE = "3600c68ed4f98edddcd54f48a1808413fae83202"
FILES = ("model.py", "run.py", "verify.py", "REPORT.md")
check, must, put = previous.check, previous.must, previous.put


def request(cid, root, operation="StageCandidatePaths", paths=None):
    result = dict(operation=operation, candidate_id=cid, expected_digest=identity(root))
    if operation == "StageCandidatePaths":
        result["paths"] = paths
    return result


def parser_cases(width):
    header = b"# branch.oid (initial)\0# branch.head main\0"
    h = b"0" * width
    ordinary = b"1 M. N... 100644 100644 100644 " + h + b" " + h + b" file\0"
    rename = b"2 R. N... 100644 100644 100644 " + h + b" " + h + b" R100 new\0old\0"
    unmerged = b"u UU N... 100644 100644 100644 100644 " + b" ".join([h] * 3) + b" conflict\0"
    positives = {
        "ordinary": header + ordinary,
        "rename": header + rename,
        "copy": header + rename.replace(b"R.", b"C.").replace(b"R100", b"C75"),
        "unmerged": header + unmerged,
        "byte-paths": header + b'? invalid-\xff\xfe\0? line\n\t"name\0! ignored\0',
        "path-at-limit": header + b"? " + b"x" * MAX_PATH + b"\0",
        "records-at-limit": header + b"? x\0" * (MAX_RECORDS - 2),
    }
    negatives = {
        "missing-nul": header[:-1],
        "missing-header": b"? x\0",
        "duplicate-header": header + header,
        "unknown-header": header + b"# future value\0",
        "unknown-kind": header + b"x data\0",
        "empty-path": header + b"? \0",
        "absolute-path": header + b"? /escape\0",
        "metadata-path": header + b"? .git/config\0",
        "dotdot-path": header + b"? a/../b\0",
        "bad-oid": header + ordinary.replace(h, b"z" * width, 1),
        "wrong-width": header + ordinary.replace(h, b"0" * (width - 1), 1),
        "bad-mode": header + ordinary.replace(b"100644", b"999999", 1),
        "submodule": header + ordinary.replace(b"N...", b"S..."),
        "bad-xy": header + ordinary.replace(b"M.", b"Z."),
        "missing-field": header + ordinary.replace(b"100644 ", b"", 1),
        "missing-rename-source": header + rename[:-4],
        "bad-score": header + rename.replace(b"R100", b"R101"),
        "extra-nul": header + b"\0",
        "path-overflow": header + b"? " + b"x" * (MAX_PATH + 1) + b"\0",
        "record-overflow": header + b"? x\0" * (MAX_RECORDS - 1),
        "output-overflow": b"x" * (MAX_OUTPUT + 1),
    }
    rows = []
    for name, raw in positives.items():
        records = parse_status(raw, width)
        check(encode_status(records) == raw, name)
        rows.append(
            dict(
                group="parser-positive",
                name=name,
                width=width,
                raw_sha256=digest(raw),
                count=len(records),
                roundtrip=True,
            )
        )
    for name, raw in negatives.items():
        try:
            parse_status(raw, width)
        except ResponseError as error:
            rows.append(
                dict(group="parser-reject", name=name, width=width, diagnostic=error.code, raw_sha256=digest(raw))
            )
        else:
            raise AssertionError("parser accepted " + name)
    # Index and tree are also validated independently of status bytes.
    for name, raw in {
        "index-width": b"100644 bad 0\tx\0",
        "index-stage": b"100644 " + h + b" 1\tx\0",
        "index-limit": b"x" * (MAX_OUTPUT + 1),
    }.items():
        try:
            index_map(raw, width)
        except ResponseError as error:
            rows.append(
                dict(group="parser-reject", name=name, width=width, diagnostic=error.code, raw_sha256=digest(raw))
            )
        else:
            raise AssertionError(name)
    return rows


def run():
    cases = []
    with tempfile.TemporaryDirectory(prefix="sec33-lab-") as scratch:
        work = Path(scratch)
        home = work / "home"
        home.mkdir()
        parent = work / "candidates"
        parent.mkdir()
        importer = SnapshotImporter(parent, home)
        for fmt, width in (("sha1", 40), ("sha256", 64)):
            cases.extend(parser_cases(width))
            for origin in ("detached", "unborn"):
                stem = fmt + "-" + origin
                source = work / ("source-" + stem)
                previous.fixture(source, fmt, origin, home)
                if origin == "detached":
                    put(source, "rename-old", "rename contents\n")
                    must(source, ["add", "--", "rename-old"], home)
                    must(source, ["commit", "--quiet", "-m", "rename base"], home)
                    (source / "rename-old").rename(source / "rename-new")
                else:
                    put(source, "rename-new", "rename contents\n")
                for name in ("tab\tname", 'quote"name'):
                    put(source, name, "literal\n")
                paths = (
                    previous.PATHS
                    + ["rename-new", "tab\tname", 'quote"name']
                    + (["rename-old"] if origin == "detached" else [])
                )
                raw = upstream.canonical(upstream.bundle_fixture(source, fmt))
                source_before = identity(source)

                def imported(suffix, payload=raw):
                    cid = stem + "-" + suffix
                    result = importer.import_bytes(cid, payload, digest(payload))
                    return cid, importer.candidates[cid], result

                cid, candidate, registration = imported("typed")
                _, native, _ = imported("native")
                _, protected, _ = imported("protected")
                protected_before = identity(protected)
                lab = TypedLab(importer, width)
                before = previous.state(candidate, home)
                native_status = must(native, STATUS, home)
                inspected = lab.execute(request(cid, candidate, "InspectCandidate"))
                check(inspected["ok"] and encode_status(inspected["status"]) == native_status, "inspect parity")
                check(identity(candidate) == before["digest"], "inspect mutation")
                req = request(cid, candidate, paths=paths)
                result = lab.execute(req)
                must(native, ["--literal-pathspecs", "add", "-A", "--", *paths], home)
                tree = must(native, ["write-tree"], home).decode().strip()
                native_after = previous.state(native, home)
                after = previous.state(candidate, home)
                native_after_status = must(native, STATUS, home)
                native_changed = sorted(
                    p.hex()
                    for p in must(
                        native,
                        ["diff", "--cached", "--name-only", "-z", "--no-renames", "--no-ext-diff", "--no-textconv"],
                        home,
                    ).split(b"\0")
                    if p
                )
                native_index_before = index_map(bytes.fromhex(before["index_hex"]), width)
                native_index_after = index_map(bytes.fromhex(native_after["index_hex"]), width)
                index_changed = sorted(
                    p
                    for p in native_index_before.keys() | native_index_after.keys()
                    if native_index_before.get(p) != native_index_after.get(p)
                )
                check(result["ok"] and result["tree_oid"] == tree, "tree parity")
                check(encode_status(result["status"]) == native_after_status, "after status parity")
                check(
                    result["changed_paths_hex"] == native_changed
                    and result["index_changed_paths_hex"] == index_changed,
                    "changed paths parity",
                )
                check(
                    result["index_digest"] == after["raw_index_digest"]
                    and after["index_hex"] == native_after["index_hex"],
                    "index parity",
                )
                check(result["candidate_digest"] == after["digest"] != before["digest"], "snapshot digest")
                check(all(before[k] == after[k] for k in ("files", "head_hex", "refs_hex")), "stage preservation")
                stale = lab.execute(req)
                check(
                    stale.get("diagnostic") == "INPUT_UNSTABLE" and identity(candidate) == after["digest"],
                    "stale accepted",
                )
                cases.append(
                    dict(
                        group="native-parity",
                        name=stem,
                        format=fmt,
                        origin=origin,
                        imported=registration,
                        before=before,
                        after=after,
                        native_after=native_after,
                        inspect=inspected,
                        stage=result,
                        native_status_hex=native_status.hex(),
                        native_after_status_hex=native_after_status.hex(),
                        native_changed_paths_hex=native_changed,
                        native_index_changed_paths_hex=index_changed,
                        native_tree=tree,
                        stale=stale,
                        stale_after=identity(candidate),
                    )
                )

                restaged = lab.execute(request(cid, candidate, paths=["modified.txt"]))
                check(
                    restaged["ok"]
                    and restaged["changed_paths_hex"] == native_changed
                    and restaged["index_changed_paths_hex"] == [],
                    "restage semantics",
                )
                cases.append(
                    dict(
                        group="restage",
                        name=stem,
                        result=restaged,
                        expected_staged=native_changed,
                        before=after["digest"],
                        after=identity(candidate),
                    )
                )

                # Deliberately mismatched trusted format must reject actual response bytes before add.
                bad_id, bad_root, _ = imported("bad-width")
                bad_before = identity(bad_root)
                bad_lab = TypedLab(importer, 64 if width == 40 else 40)
                bad_result = bad_lab.execute(request(bad_id, bad_root, paths=paths))
                # Unborn reaches post-add tree validation; its initial status has no object OIDs.
                if bad_result.get("ok"):
                    raise AssertionError("wrong trusted width accepted")
                cases.append(
                    dict(
                        group="response-failure",
                        name=stem + "-width",
                        before=bad_before,
                        after=identity(bad_root),
                        result=bad_result,
                    )
                )

                # Lower only the parser bound after add; real post-stage status now exceeds it.
                limit_id, limit_root, _ = imported("output-limit")
                limit_before = identity(limit_root)

                def lower_limit(root):
                    model.MAX_OUTPUT = 128

                limit_lab = TypedLab(importer, width, after_add=lower_limit)
                try:
                    limited = limit_lab.execute(request(limit_id, limit_root, paths=paths))
                finally:
                    model.MAX_OUTPUT = MAX_OUTPUT
                check(
                    limited.get("diagnostic") == "OUTPUT_LIMIT" and limited["execution_started"], "output failure lost"
                )
                check(
                    limited["candidate_digest"] == identity(limit_root) != limit_before, "overflow partial state lost"
                )
                cases.append(
                    dict(
                        group="response-failure",
                        name=stem + "-limit",
                        before=limit_before,
                        after=identity(limit_root),
                        result=limited,
                    )
                )

                wire_id, wire_root, _ = imported("wire-limit")
                wire_before = identity(wire_root)

                def lower_wire(root):
                    model.MAX_WIRE = 256

                wire_lab = TypedLab(importer, width, after_add=lower_wire)
                try:
                    wire_result = wire_lab.execute(request(wire_id, wire_root, paths=paths))
                finally:
                    model.MAX_WIRE = 8 * MAX_OUTPUT
                check(
                    wire_result.get("diagnostic") == "OUTPUT_LIMIT"
                    and wire_result["observations_omitted"]
                    and wire_result["recovery_required"],
                    "wire overflow not explicit",
                )
                check(wire_result["candidate_digest"] == identity(wire_root) != wire_before, "wire overflow state")
                cases.append(
                    dict(
                        group="response-failure",
                        name=stem + "-wire",
                        before=wire_before,
                        after=identity(wire_root),
                        result=wire_result,
                    )
                )

                # Actual add succeeds and writes the index; actual write-tree then fails to acquire a lock.
                fail_id, failed, _ = imported("partial")
                fail_before = previous.state(failed, home)
                failure_lab = TypedLab(
                    importer, width, after_add=lambda root: (root / ".git/index.lock").write_bytes(b"fixture lock\n")
                )
                failure = failure_lab.execute(request(fail_id, failed, paths=paths))
                fail_after = previous.state(failed, home)
                check(
                    not failure["ok"] and failure["diagnostic"] == "EXECUTION_FAILED" and failure["exit"] != 0,
                    "expected real write-tree failure",
                )
                check(
                    failure["execution_started"] and fail_before["raw_index_digest"] != fail_after["raw_index_digest"],
                    "partial index missing",
                )
                check(
                    failure["candidate_digest"] == fail_after["digest"] and failure["changed_entries"],
                    "partial state lost",
                )
                check(
                    all(fail_before[k] == fail_after[k] for k in ("files", "head_hex", "refs_hex")),
                    "failure live files",
                )
                quarantined = failure_lab.execute(request(fail_id, failed, "InspectCandidate"))
                check(quarantined["diagnostic"] == "CANDIDATE_QUARANTINED", "quarantine bypass")
                cases.append(
                    dict(
                        group="execution-failure",
                        name=stem,
                        before=fail_before,
                        after=fail_after,
                        result=failure,
                        retry=quarantined,
                        commands=failure_lab.commands,
                    )
                )
                # Actual Git add failure before index write remains an execution failure, not preflight denial.
                missing_id, missing, _ = imported("missing")
                missing_lab = TypedLab(importer, width)
                missing_before = identity(missing)
                missing_result = missing_lab.execute(request(missing_id, missing, paths=["absent-path"]))
                check(
                    missing_result.get("diagnostic") == "EXECUTION_FAILED" and missing_result["execution_started"],
                    "missing path not execution failure",
                )
                cases.append(
                    dict(
                        group="add-failure",
                        name=stem,
                        before=missing_before,
                        after=identity(missing),
                        result=missing_result,
                    )
                )

                diag_data = json.loads(raw)
                diag_paths = [str(i) + "x" * 200 + "/ignored.txt" for i in range(30)]
                for diag_path in diag_paths:
                    upstream.set_file(diag_data, diag_path, b"ignored\n")
                diag_id, diag_root, _ = imported("diagnostic", upstream.canonical(diag_data))
                diag_result = TypedLab(importer, width).execute(request(diag_id, diag_root, paths=diag_paths))
                check(
                    diag_result.get("diagnostic") == "EXECUTION_FAILED"
                    and diag_result["stderr_truncated"]
                    and len(bytes.fromhex(diag_result["stderr_hex"])) == 4096,
                    "diagnostic not bounded",
                )
                cases.append(dict(group="diagnostic-bound", name=stem, result=diag_result, actual=identity(diag_root)))

                # Whole-candidate helper/hook refusals still happen before stage or observations.
                data = json.loads(raw)
                config = bytes.fromhex(upstream.entry(data, ".git/config")["hex"])
                for name, path, payload, mode, diagnostic in (
                    (
                        "filter",
                        ".git/config",
                        config + b'[filter "demo"]\nclean=false\nrequired=true\n',
                        0o644,
                        "UNSUPPORTED_HELPER",
                    ),
                    ("attribute", ".gitattributes", b"*.txt filter=demo\n", 0o644, "UNSUPPORTED_HELPER"),
                    ("hook", ".git/hooks/pre-commit", b"#!/bin/sh\nexit 99\n", 0o755, "UNSUPPORTED_HOOK"),
                ):
                    variant = json.loads(raw)
                    upstream.set_file(variant, path, payload, mode)
                    deny_id, denied, _ = imported(name, upstream.canonical(variant))
                    for op in ("InspectCandidate", "StageCandidatePaths"):
                        denied_lab = TypedLab(importer, width)
                        original = identity(denied)
                        outcome = denied_lab.execute(request(deny_id, denied, op, paths))
                        check(
                            outcome.get("diagnostic") == diagnostic
                            and identity(denied) == original
                            and not denied_lab.commands,
                            "helper/hook accepted",
                        )
                        cases.append(
                            dict(
                                group="preflight-deny",
                                name=stem + "-" + name + "-" + op,
                                before=original,
                                after=identity(denied),
                                result=outcome,
                                commands=denied_lab.commands,
                            )
                        )
                cases.append(
                    dict(
                        group="preservation",
                        name=stem,
                        bundle_before=digest(raw),
                        bundle_after=digest(raw),
                        source_before=source_before,
                        source_after=identity(source),
                        protected_before=protected_before,
                        protected_after=identity(protected),
                    )
                )
                check(
                    source_before == identity(source) and protected_before == identity(protected),
                    "source/other candidate mutation",
                )
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
        git=git(HERE, ["--version"], Path("/tmp")).stdout.decode().strip(),
        python=platform.python_version(),
        contract_sha256=digest(previous.CONTRACT.read_bytes()),
        upstream_hashes={
            str(p.relative_to(ROOT)): digest(p.read_bytes())
            for folder, names in (
                (IMPORT, ("importer.py", "run.py", "verify.py", "results.json")),
                (previous.HERE, ("adapter.py", "run.py", "verify.py", "results.json")),
            )
            for name in names
            for p in [folder / name]
        },
        source_hashes={str(p.relative_to(ROOT)): digest(p.read_bytes()) for p in sorted((ROOT / "src").rglob("*.py"))},
        artifact_hashes={name: digest((HERE / name).read_bytes()) for name in FILES},
        cases=cases,
    )


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, sort_keys=True))
