# ruff: noqa: INP001, PT027, S101, S603, S607
# unittest keeps the transport checks runnable with the stdlib-only exporter.
"""Exercise candidate transport using disposable repositories; no trusted verdicts."""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from artifacts.gate1.prepare import prepare


class CandidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="aconsole-candidate-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / "repo"
        self.root.mkdir()
        self.output = Path(self.temporary.name) / "packet"
        self.git("init", "-q")
        self.git("config", "user.name", "candidate test")
        self.git("config", "user.email", "candidate@example.invalid")
        (self.root / "src").mkdir()
        (self.root / "src/main.py").write_bytes(b"print('committed')\n")
        self.commit()

    def git(self, *args: str) -> bytes:
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=True, capture_output=True, timeout=10,
        ).stdout

    def commit(self) -> None:
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")

    def test_worktree_and_staged_edits_never_replace_committed_bytes(self) -> None:
        (self.root / "src/main.py").write_bytes(b"staged\n")
        self.git("add", ".")
        (self.root / "src/main.py").write_bytes(b"unstaged\n")
        (self.root / "src/private.txt").write_bytes(b"untracked\n")
        result = prepare(self.root, self.output)
        with zipfile.ZipFile(self.output / "candidate.zip") as bundle:
            assert bundle.namelist() == ["source/src/main.py"]
            assert bundle.read("source/src/main.py") == b"print('committed')\n"
        assert result["acceptance"] == "UNVERIFIED"

    def test_export_attributes_do_not_omit_or_substitute_inputs(self) -> None:
        (self.root / ".gitattributes").write_text("src/main.py export-ignore\nsrc/subst export-subst\n")
        (self.root / "src/subst").write_bytes(b"$Format:%H$\n")
        self.commit()
        prepare(self.root, self.output)
        with zipfile.ZipFile(self.output / "candidate.zip") as bundle:
            assert bundle.read("source/src/subst") == b"$Format:%H$\n"
            assert "source/src/main.py" in bundle.namelist()

    def test_explicit_revision_reproduces_an_older_candidate(self) -> None:
        head = self.git("rev-parse", "HEAD").decode().strip()
        first = prepare(self.root, self.output)
        (self.root / "src/main.py").write_bytes(b"new revision\n")
        self.commit()
        second = prepare(self.root, self.output.with_name("historical"), head)
        assert first == second

    def test_hashes_modes_and_archive_are_reproducible(self) -> None:
        (self.root / "src/main.py").chmod(0o755)
        self.commit()
        first = prepare(self.root, self.output)
        second_path = self.output.with_name("second")
        second = prepare(self.root, second_path)
        assert first == second
        data = json.loads((self.output / "candidate.json").read_text())
        with zipfile.ZipFile(self.output / "candidate.zip") as bundle:
            for path, metadata in data["files"].items():
                info = bundle.getinfo("source/" + path)
                assert info.external_attr >> 16 == int(metadata["git_mode"], 8)
                assert hashlib.sha256(bundle.read(info)).hexdigest() == metadata["sha256"]
        assert data["archive_sha256"] == hashlib.sha256((self.output / "candidate.zip").read_bytes()).hexdigest()

    def test_existing_results_are_never_overwritten(self) -> None:
        prepare(self.root, self.output)
        before = {path.name: path.read_bytes() for path in self.output.iterdir()}
        with self.assertRaises(FileExistsError):
            prepare(self.root, self.output)
        assert before == {path.name: path.read_bytes() for path in self.output.iterdir()}

    def test_symlinks_are_rejected_without_reading_the_target(self) -> None:
        (self.root / "src/link").symlink_to("/file-outside-candidate")
        self.commit()
        with self.assertRaisesRegex(ValueError, "Unsupported review input"):
            prepare(self.root, self.output)
        assert not self.output.exists()

    def test_submodules_are_rejected(self) -> None:
        head = self.git("rev-parse", "HEAD").decode().strip()
        self.git("update-index", "--add", "--cacheinfo", f"160000,{head},src/module")
        self.git("commit", "-qm", "gitlink fixture")
        with self.assertRaisesRegex(ValueError, "Unsupported review input"):
            prepare(self.root, self.output)
        assert not self.output.exists()


if __name__ == "__main__":
    unittest.main()
