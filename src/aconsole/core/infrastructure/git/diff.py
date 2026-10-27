"""Worktree diffs. Every `git diff` argv carries `--no-ext-diff` and `--no-textconv`.

Those two flags are placed before `--`, so a `.gitattributes` diff driver does not run.
This module does not invoke any other git subcommand.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Final, final

from aconsole.core.application.ports.diff_reader import (
    DiffChange,
    DiffFileStat,
    DiffFileText,
    DiffHunkView,
    DiffKind,
    DiffReadError,
    DiffSummary,
    DiffWordLine,
)

_TIMEOUT_S: Final = 30.0
_MAX_BYTES: Final = 2_000_000
_OK: Final = 0
_GIT: Final = Path("/usr/bin/git")
_HEAD: Final = "HEAD"
_WORKTREE: Final = "WORKTREE"
_NO_EXT: Final = "--no-ext-diff"
_NO_TEXT: Final = "--no-textconv"
_FAILED: Final = "git command failed"
_TIMED_OUT: Final = "git timed out"
_MISSING: Final = "git binary missing"
_ABS_GIT: Final = "git binary path must be absolute"
_ABS_REPO: Final = "repository path must be absolute"
_FLAGS: Final = "git diff is missing --no-ext-diff or --no-textconv"
_TOO_LARGE: Final = "diff output is over the byte cap"
_BAD_STATUS: Final = "git name-status line was unreadable"
_BAD_NUMSTAT: Final = "git numstat line was unreadable"
_BINARY_MARK: Final = "-"
_HUNK: Final = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_STATUS_ONE: Final = frozenset({"A", "M", "D", "T", "U"})
_PAIR: Final = 2
_NUMSTAT_FIELDS: Final = 3
_AT_LEAST_ONE: Final = 1


@dataclass(frozen=True, slots=True, kw_only=True)
class _Row:
    change: DiffChange
    path: str
    old_path: str | None


@dataclass(frozen=True, slots=True, kw_only=True)
class _Count:
    binary: bool
    insertions: int
    deletions: int


@dataclass(frozen=True, slots=True, kw_only=True)
class _Token:
    mark: str
    text: str


@final
class GitDiff:
    """`git diff` against HEAD, or against the base the caller names."""

    _git: Path
    _timeout_s: float
    _max_bytes: int

    def __init__(
        self,
        git: Path = _GIT,
        *,
        timeout_s: float = _TIMEOUT_S,
        max_bytes: int = _MAX_BYTES,
    ) -> None:
        if not git.is_absolute():
            raise DiffReadError(_ABS_GIT, too_large=False, files=0, byte_count=0)
        if timeout_s <= 0 or max_bytes <= 0:
            message = "timeout and byte cap must be positive"
            raise DiffReadError(message, too_large=False, files=0, byte_count=0)
        self._git = git
        self._timeout_s = timeout_s
        self._max_bytes = max_bytes

    def summary(self, repo: Path, base: str | None) -> DiffSummary:
        spec = _base(base)
        status = self._stdout(repo, ("--name-status", "-z", "-M", spec))
        numbers = self._stdout(repo, ("--numstat", "-z", "-M", spec))
        files = _join(_parse_status(status), _parse_numstat(numbers))
        return DiffSummary(base=spec, head=_WORKTREE, files=files)

    def file(self, repo: Path, path: str, base: str | None, max_lines: int) -> DiffFileText:
        text = self._stdout(
            repo,
            ("-M", "--word-diff=porcelain", "-U3", _base(base), "--", path),
        )
        if _is_binary_diff(text):
            return DiffFileText(hunks=(), truncated=False)
        return _parse_porcelain(text, max_lines)

    def _stdout(self, repo: Path, extra: tuple[str, ...]) -> str:
        _absolute_repo(repo)
        argv = ("diff", _NO_EXT, _NO_TEXT, *extra)
        _require_flags(argv)
        try:
            completed = subprocess.run(  # noqa: S603 -- argv is a list; the git path is absolute
                (str(self._git), "-C", str(repo), *argv),
                check=False,
                capture_output=True,
                timeout=self._timeout_s,
                env=os.environ.copy(),
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired as exc:
            raise DiffReadError(_TIMED_OUT, too_large=False, files=0, byte_count=0) from exc
        except FileNotFoundError as exc:
            raise DiffReadError(_MISSING, too_large=False, files=0, byte_count=0) from exc
        if completed.returncode != _OK:
            raise DiffReadError(_FAILED, too_large=False, files=0, byte_count=0)
        raw = completed.stdout
        size = len(raw.encode("utf-8"))
        if size > self._max_bytes:
            raise DiffReadError(
                _TOO_LARGE,
                too_large=True,
                files=max(_AT_LEAST_ONE, raw.count("\0")),
                byte_count=size,
            )
        return raw


def _base(base: str | None) -> str:
    if base is None or base == "":
        return _HEAD
    return base


def _absolute_repo(repo: Path) -> None:
    if not repo.is_absolute():
        raise DiffReadError(_ABS_REPO, too_large=False, files=0, byte_count=0)


def _require_flags(argv: tuple[str, ...]) -> None:
    end = argv.index("--") if "--" in argv else len(argv)
    head = argv[:end]
    if _NO_EXT not in head or _NO_TEXT not in head:
        raise DiffReadError(_FLAGS, too_large=False, files=0, byte_count=0)


def _parse_status(raw: str) -> tuple[_Row, ...]:
    parts = [piece for piece in raw.split("\0") if piece]
    rows: list[_Row] = []
    index = 0
    while index < len(parts):
        code = parts[index]
        index += 1
        letter = code[:1]
        if letter in {"R", "C"}:
            rows.append(_rename_row(parts, index))
            index += _PAIR
            continue
        if letter not in _STATUS_ONE:
            raise DiffReadError(_BAD_STATUS, too_large=False, files=0, byte_count=0)
        if index >= len(parts):
            raise DiffReadError(_BAD_STATUS, too_large=False, files=0, byte_count=0)
        rows.append(_Row(change=_one_change(letter), path=parts[index], old_path=None))
        index += 1
    return tuple(rows)


def _rename_row(parts: list[str], index: int) -> _Row:
    if index + 1 >= len(parts):
        raise DiffReadError(_BAD_STATUS, too_large=False, files=0, byte_count=0)
    return _Row(change="renamed", path=parts[index + 1], old_path=parts[index])


def _one_change(letter: str) -> DiffChange:
    if letter == "A":
        return "added"
    if letter == "D":
        return "deleted"
    return "modified"


def _parse_numstat(raw: str) -> dict[str, _Count]:
    """`-z` renames are `ins\\tdel\\t`, then the old path, then the new path."""
    parts = raw.split("\0")
    counts: dict[str, _Count] = {}
    index = 0
    while index < len(parts):
        piece = parts[index]
        if piece == "":
            index += _AT_LEAST_ONE
            continue
        fields = piece.split("\t", 2)
        if len(fields) < _NUMSTAT_FIELDS:
            raise DiffReadError(_BAD_NUMSTAT, too_large=False, files=0, byte_count=0)
        path = fields[2]
        step = _AT_LEAST_ONE
        if path == "":
            new_at = index + _PAIR
            if new_at >= len(parts) or parts[new_at] == "":
                raise DiffReadError(_BAD_NUMSTAT, too_large=False, files=0, byte_count=0)
            path = parts[new_at]
            step = _PAIR + _AT_LEAST_ONE
        counts[path] = _count(fields[0], fields[1])
        index += step
    return counts


def _count(insertions: str, deletions: str) -> _Count:
    if _BINARY_MARK in (insertions, deletions):
        return _Count(binary=True, insertions=0, deletions=0)
    return _Count(binary=False, insertions=int(insertions), deletions=int(deletions))


def _join(rows: tuple[_Row, ...], counts: dict[str, _Count]) -> tuple[DiffFileStat, ...]:
    files: list[DiffFileStat] = []
    for row in rows:
        count = counts.get(row.path)
        change = row.change
        insertions = 0
        deletions = 0
        if count is not None and count.binary:
            change = "binary"
        elif count is not None:
            insertions = count.insertions
            deletions = count.deletions
        files.append(
            DiffFileStat(
                path=row.path,
                old_path=row.old_path,
                change=change,
                insertions=insertions,
                deletions=deletions,
            )
        )
    return tuple(files)


def _is_binary_diff(text: str) -> bool:
    prefixes = ("Binary files ", "GIT binary patch")
    return any(line.startswith(prefixes) for line in text.splitlines())


def _parse_porcelain(text: str, max_lines: int) -> DiffFileText:
    parser = _Porcelain(max_lines)
    return parser.parse(text)


@final
class _Porcelain:
    """Turn `--word-diff=porcelain` into hunks, stopping at `max_lines`."""

    _max_lines: int
    _hunks: list[DiffHunkView]
    _taken: list[DiffWordLine]
    _header: str
    _old_start: int
    _old_lines: int
    _new_start: int
    _new_lines: int
    _old_no: int
    _new_no: int
    _tokens: list[_Token]
    _truncated: bool
    _in_hunk: bool

    def __init__(self, max_lines: int) -> None:
        self._max_lines = max_lines
        self._hunks = []
        self._taken = []
        self._header = ""
        self._old_start = 0
        self._old_lines = 0
        self._new_start = 0
        self._new_lines = 0
        self._old_no = 0
        self._new_no = 0
        self._tokens = []
        self._truncated = False
        self._in_hunk = False

    def parse(self, text: str) -> DiffFileText:
        for line in text.splitlines():
            if self._truncated:
                break
            self._one(line)
        if self._in_hunk and (not self._truncated or len(self._taken) > 0):
            self._close()
        return DiffFileText(hunks=tuple(self._hunks), truncated=self._truncated)

    def _one(self, line: str) -> None:
        matched = _HUNK.match(line)
        if matched is not None:
            self._start(line, matched)
            return
        if not self._in_hunk:
            return
        if line == "~":
            self._end_logical()
            return
        if line.startswith("\\"):
            self._meta(line)
            return
        self._tokens.append(_Token(mark=line[:1], text=line[1:]))

    def _start(self, line: str, matched: re.Match[str]) -> None:
        if self._in_hunk:
            self._close()
        self._header = line
        self._old_start, self._old_lines, self._new_start, self._new_lines = _hunk_numbers(matched)
        self._old_no = self._old_start
        self._new_no = self._new_start
        self._tokens = []
        self._taken = []
        self._in_hunk = True

    def _end_logical(self) -> None:
        produced, self._old_no, self._new_no = _finish(self._tokens, self._old_no, self._new_no)
        self._tokens = []
        if len(self._taken) + len(produced) > self._max_lines:
            self._truncated = True
            return
        self._taken.extend(produced)

    def _meta(self, line: str) -> None:
        if len(self._taken) + 1 > self._max_lines:
            self._truncated = True
            return
        self._taken.append(DiffWordLine(kind="meta", old_no=None, new_no=None, text=line, word_spans=()))

    def _close(self) -> None:
        self._hunks.append(
            DiffHunkView(
                id=self._header,
                header=self._header,
                old_start=self._old_start,
                old_lines=self._old_lines,
                new_start=self._new_start,
                new_lines=self._new_lines,
                lines=tuple(self._taken),
            )
        )


def _hunk_numbers(matched: re.Match[str]) -> tuple[int, int, int, int]:
    old_count = matched.group(2)
    new_count = matched.group(4)
    return (
        int(matched.group(1)),
        1 if old_count is None else int(old_count),
        int(matched.group(3)),
        1 if new_count is None else int(new_count),
    )


def _finish(tokens: list[_Token], old_no: int, new_no: int) -> tuple[list[DiffWordLine], int, int]:
    has_minus = any(token.mark == "-" for token in tokens)
    has_plus = any(token.mark == "+" for token in tokens)
    produced: list[DiffWordLine] = []
    if not has_minus and not has_plus:
        produced.append(_line("context", old_no, new_no, tokens, " "))
        return produced, old_no + 1, new_no + 1
    if has_minus:
        produced.append(_line("del", old_no, None, [token for token in tokens if token.mark != "+"], "-"))
        old_no += 1
    if has_plus:
        produced.append(_line("add", None, new_no, [token for token in tokens if token.mark != "-"], "+"))
        new_no += 1
    return produced, old_no, new_no


def _line(
    kind: DiffKind,
    old_no: int | None,
    new_no: int | None,
    tokens: list[_Token],
    changed: str,
) -> DiffWordLine:
    text, spans = _spans(tokens, changed)
    return DiffWordLine(kind=kind, old_no=old_no, new_no=new_no, text=text, word_spans=spans)


def _spans(tokens: list[_Token], changed: str) -> tuple[str, tuple[tuple[int, int], ...]]:
    parts: list[str] = []
    spans: list[tuple[int, int]] = []
    at = 0
    saw_common = False
    for token in tokens:
        parts.append(token.text)
        if token.mark == " ":
            saw_common = True
        elif token.mark == changed:
            spans.append((at, at + len(token.text)))
        at += len(token.text)
    if not saw_common:
        return "".join(parts), ()
    return "".join(parts), tuple(spans)


