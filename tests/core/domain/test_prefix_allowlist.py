"""The prefix allowlist: entries, flags, `--`, and `=` handling for `allowed_by_entry`."""

import pytest

from aconsole.core.domain.prefix_allowlist import (
    ALLOWLIST,
    ALLOWLIST_VERSION,
    GIT_ENV,
    AllowEntry,
    allowed_by_entry,
    entry_for_prefix,
)


def _entry(program: str, subcommand: str | None) -> AllowEntry:
    for entry in ALLOWLIST:
        if entry.program == program and entry.subcommand == subcommand:
            return entry
    message = f"no allowlist entry for {program} {subcommand}"
    raise LookupError(message)


def test_version_and_git_environment() -> None:
    assert ALLOWLIST_VERSION == 2
    assert dict(GIT_ENV) == {
        "GIT_PAGER": "cat",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.fsmonitor",
        "GIT_CONFIG_VALUE_0": "false",
    }


def test_pytest_is_not_allowlisted() -> None:
    assert all(entry.program != "pytest" for entry in ALLOWLIST)


def test_entry_for_prefix_requires_the_exact_program_and_subcommand_shape() -> None:
    assert entry_for_prefix(("git", "status")) is _entry("git", "status")
    assert entry_for_prefix(("ls",)) is _entry("ls", None)
    assert entry_for_prefix(("git",)) is None
    assert entry_for_prefix(("git", "push")) is None
    assert entry_for_prefix(("cat", "build")) is None
    assert entry_for_prefix(("npm", "test")) is None


@pytest.mark.parametrize(
    ("program", "subcommand", "words", "expected"),
    [
        # git status: each listed flag, a refused flag, a combined flag, and `=` forms
        ("git", "status", ("git", "status"), True),
        ("git", "status", ("git", "status", "-s"), True),
        ("git", "status", ("git", "status", "--short"), True),
        ("git", "status", ("git", "status", "-b", "--branch", "--porcelain"), True),
        ("git", "status", ("git", "status", "--ignored=no"), True),
        ("git", "status", ("git", "status", "--ignored=all"), False),
        ("git", "status", ("git", "status", "--ignored"), False),
        ("git", "status", ("git", "status", "-u"), False),
        ("git", "status", ("git", "status", "-sb"), True),
        ("git", "status", ("git", "status", "-su"), False),
        ("git", "status", ("git", "status", "--short=x"), False),
        ("git", "status", ("git", "status", "src/app.py"), False),
        ("git", "status", ("git", "status", "x=y"), False),
        ("git", "status", ("git", "stat"), False),
        ("git", "status", ("git", "log"), False),
        ("git", "status", ("git", "-c", "core.pager=x", "status"), False),
        # git log: value flag `-n`, a refused `--output`, `--exec`, `-c`, and a combined value flag.
        # Version 2: `-p` and `--stat` run the diff drivers, so they need both `--no-ext-diff` and `--no-textconv`.
        ("git", "log", ("git", "log", "--oneline", "--graph", "--name-only"), True),
        ("git", "log", ("git", "log", "--oneline", "--graph", "--stat", "--name-only"), False),
        (
            "git",
            "log",
            ("git", "log", "--oneline", "--graph", "--stat", "--name-only", "--no-ext-diff", "--no-textconv"),
            True,
        ),
        ("git", "log", ("git", "log", "--no-color", "--decorate", "-p", "--all"), False),
        (
            "git",
            "log",
            ("git", "log", "--no-color", "--decorate", "-p", "--all", "--no-ext-diff", "--no-textconv"),
            True,
        ),
        ("git", "log", ("git", "log", "-p", "--no-textconv", "--no-ext-diff"), True),
        ("git", "log", ("git", "log", "--no-ext-diff", "-p", "--no-textconv", "--oneline"), True),
        ("git", "log", ("git", "log", "-p", "--no-textconv"), False),
        ("git", "log", ("git", "log", "-p", "--no-ext-diff"), False),
        ("git", "log", ("git", "log", "-p", "--", "--no-ext-diff", "--no-textconv"), False),
        ("git", "log", ("git", "log", "-n", "5"), True),
        ("git", "log", ("git", "log", "-n", "5", "--", "src"), True),
        ("git", "log", ("git", "log", "-n"), False),
        ("git", "log", ("git", "log", "-n", "-5"), False),
        ("git", "log", ("git", "log", "-n5"), False),
        ("git", "log", ("git", "log", "-pn", "5"), False),
        ("git", "log", ("git", "log", "--output=x"), False),
        ("git", "log", ("git", "log", "--exec", "x"), False),
        ("git", "log", ("git", "log", "-c"), False),
        ("git", "log", ("git", "log", "--format=%H"), False),
        # git diff: `--output` and `--ext-diff` refused, `--no-index` refused, `--cached` and `--staged` allowed.
        # Version 2: both `--no-ext-diff` and `--no-textconv` are always required, and only before `--`.
        ("git", "diff", ("git", "diff", "--stat", "--cached"), False),
        ("git", "diff", ("git", "diff", "--stat", "--cached", "--no-ext-diff", "--no-textconv"), True),
        ("git", "diff", ("git", "diff", "--stat", "--cached", "--no-ext-diff"), False),
        ("git", "diff", ("git", "diff", "--staged", "--name-status", "HEAD"), False),
        ("git", "diff", ("git", "diff", "--staged", "--name-status", "HEAD", "--no-textconv", "--no-ext-diff"), True),
        ("git", "diff", ("git", "diff", "--output=x"), False),
        ("git", "diff", ("git", "diff", "--ext-diff"), False),
        ("git", "diff", ("git", "diff", "--no-index", "a", "b"), False),
        ("git", "diff", ("git", "diff", "--", "src"), False),
        ("git", "diff", ("git", "diff", "--no-ext-diff", "--no-textconv", "--", "src"), True),
        ("git", "diff", ("git", "diff", "--", "--no-ext-diff", "--no-textconv"), False),
        # git show: plain `HEAD:path` is a word, `--textconv` and `--output` are refused
        ("git", "show", ("git", "show", "--stat", "HEAD:README.md"), False),
        ("git", "show", ("git", "show", "--stat", "HEAD:README.md", "--no-ext-diff", "--no-textconv"), True),
        ("git", "show", ("git", "show", "--output=x"), False),
        ("git", "show", ("git", "show", "--textconv", "HEAD"), False),
        # git branch: no positional word, because a plain name would create a branch
        ("git", "branch", ("git", "branch"), True),
        ("git", "branch", ("git", "branch", "--list"), True),
        ("git", "branch", ("git", "branch", "-a", "-v"), True),
        ("git", "branch", ("git", "branch", "-D", "x"), False),
        ("git", "branch", ("git", "branch", "-d", "x"), False),
        ("git", "branch", ("git", "branch", "-m", "a", "b"), False),
        ("git", "branch", ("git", "branch", "x"), False),
        ("git", "branch", ("git", "branch", "--", "x"), False),
        # ls: `-la` is `-l` and `-a` together, so it needs both flags listed
        ("ls", None, ("ls",), True),
        ("ls", None, ("ls", "-la"), True),
        ("ls", None, ("ls", "-l", "-a", "-h"), True),
        ("ls", None, ("ls", "-lah"), True),
        ("ls", None, ("ls", "-1"), True),
        ("ls", None, ("ls", "src"), True),
        ("ls", None, ("ls", "/etc"), False),
        ("ls", None, ("ls", "-x"), False),
        ("ls", None, ("ls", "--color=always"), False),
        ("ls", None, ("ls", "-la", ".."), False),
        # cat: no flags at all, so `-n` is refused
        ("cat", None, ("cat", "README.md"), True),
        ("cat", None, ("cat", "x", "y"), True),
        ("cat", None, ("cat", "a/b"), False),
        ("cat", None, ("cat", "-n", "x"), False),
        ("cat", None, ("cat", "a=b"), False),
        ("cat", None, ("cat", ".hidden"), False),
        ("cat", None, ("cat", "/etc/shadow"), False),
        ("cat", None, ("cat", "a..b"), False),
        # rg: value flags `-g` and `-t`, refused `--pre` and `--pre-glob`, `-C` not listed
        ("rg", None, ("rg", "-n", "TODO", "src"), True),
        ("rg", None, ("rg", "-i", "-S", "-l", "-c", "--no-heading", "--hidden", "x"), True),
        ("rg", None, ("rg", "-g", "src", "x"), True),
        ("rg", None, ("rg", "-t", "py", "foo"), True),
        ("rg", None, ("rg", "-g"), False),
        ("rg", None, ("rg", "-g", "-x", "foo"), False),
        ("rg", None, ("rg", "--pre", "x", "foo"), False),
        ("rg", None, ("rg", "--pre-glob", "x", "foo"), False),
        ("rg", None, ("rg", "-C", "3", "foo"), False),
        ("rg", None, ("rg", "-z", "foo"), False),
        ("rg", None, ("rg", "--hidden=x", "foo"), False),
        # `--` ends flag parsing: only plain words may follow it
        ("git", "status", ("git", "status", "--", "src/app.py"), False),
        ("git", "status", ("git", "status", "--", "-x"), False),
        ("git", "status", ("git", "status", "--", "src", "--short"), False),
        ("git", "status", ("git", "status", "--", "--"), False),
        ("git", "status", ("git", "status", "--", "a/../b"), False),
        ("git", "status", ("git", "status", "--", "/etc"), False),
        ("cat", None, ("cat", "--", "x"), True),
        ("cat", None, ("cat", "--", "-n"), False),
        ("ls", None, ("ls", "--", "-l"), False),
    ],
)
def test_allowed_by_entry(
    program: str,
    subcommand: str | None,
    words: tuple[str, ...],
    *,
    expected: bool,
) -> None:
    assert allowed_by_entry(_entry(program, subcommand), words) is expected


def test_program_and_subcommand_must_match_the_entry() -> None:
    assert allowed_by_entry(_entry("git", "status"), ("git", "log")) is False
    assert allowed_by_entry(_entry("git", "status"), ("gitx", "status")) is False
    assert allowed_by_entry(_entry("cat", None), ("ls", "x")) is False
    assert allowed_by_entry(_entry("cat", None), ()) is False


def test_combined_short_flags_need_each_letter_listed() -> None:
    only_long = AllowEntry(program="ls", subcommand=None, flags=frozenset({"-l"}), value_flags=frozenset())
    assert allowed_by_entry(only_long, ("ls", "-l")) is True
    assert allowed_by_entry(only_long, ("ls", "-la")) is False


def test_required_if_flag_counts_inside_a_combined_short_flag() -> None:
    entry = AllowEntry(
        program="git",
        subcommand="log",
        flags=frozenset({"-p", "-a", "--no-ext-diff", "--no-textconv"}),
        value_flags=frozenset(),
        required=frozenset({"--no-ext-diff", "--no-textconv"}),
        required_if=frozenset({"-p"}),
    )
    assert allowed_by_entry(entry, ("git", "log", "-pa")) is False
    assert allowed_by_entry(entry, ("git", "log", "-pa", "--no-ext-diff", "--no-textconv")) is True
    assert allowed_by_entry(entry, ("git", "log", "-a")) is True
