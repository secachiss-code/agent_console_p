"""Allowlist of (program, subcommand) pairs that a remembered tab rule may cover by prefix.

The allowlist is data, versioned by `ALLOWLIST_VERSION`. Every flag was checked against the installed programs:
git 2.47.3 (`git <sub> -h`, and a run of each flag), GNU ls and cat 9.7 (`--help`), ripgrep 14.1.1 (`--help`).
A flag that the installed version does not accept is not listed.

pytest is not listed: it imports conftest.py and other code from the repository, so a remembered prefix would run
that code with any later arguments. Only the flag list could be limited, and that does not close the risk.

`git diff`, `git show` and `git log -p`/`--stat` run `diff.external` and textconv drivers named in the repository
config, so a prefix covers them only when the command carries both `--no-ext-diff` and `--no-textconv` (version 2).
Residual risk: `filter.<driver>.clean` from the repository config still runs on worktree files; the launcher (A5)
keeps the agent from writing `.git/config`.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

ALLOWLIST_VERSION: Final = 2

# Git reads the environment of the launch. The launcher applies this mapping (A5); it is only a constant here.
# GIT_EXTERNAL_DIFF is left unset. Pager and fsmonitor run code from the environment or from the repository config.
GIT_ENV: Final[Mapping[str, str]] = MappingProxyType(
    {
        "GIT_PAGER": "cat",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.fsmonitor",
        "GIT_CONFIG_VALUE_0": "false",
    }
)

_NO_DRIVERS: Final = frozenset({"--no-ext-diff", "--no-textconv"})

_PLAIN_WORD = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.:@+-]*")
_SHORT_FLAGS = re.compile(r"-[A-Za-z0-9]{2,}")


@dataclass(frozen=True)
class AllowEntry:
    """One program with an optional subcommand, and the flags its arguments may use.

    `subcommand=None` means the program takes no subcommand. `value_flags` consume the next word as their value.
    `positional=False` refuses every plain word, for a subcommand whose plain word would write (`git branch name`).
    `required` flags must all appear before any `--` (they are also allowed flags). When `required_if` is not empty,
    they are required only once one of those flags appears.
    """

    program: str
    subcommand: str | None
    flags: frozenset[str]
    value_flags: frozenset[str]
    positional: bool = True
    required: frozenset[str] = frozenset()
    required_if: frozenset[str] = frozenset()


ALLOWLIST: Final[tuple[AllowEntry, ...]] = (
    AllowEntry(
        program="git",
        subcommand="status",
        flags=frozenset({"-s", "--short", "-b", "--branch", "--porcelain", "--ignored=no"}),
        value_flags=frozenset(),
    ),
    AllowEntry(
        program="git",
        subcommand="log",
        flags=frozenset({"--oneline", "--graph", "--stat", "--name-only", "--no-color", "--decorate", "-p", "--all"})
        | _NO_DRIVERS,
        value_flags=frozenset({"-n"}),
        required=_NO_DRIVERS,
        required_if=frozenset({"-p", "--stat"}),
    ),
    AllowEntry(
        program="git",
        subcommand="diff",
        flags=frozenset({"--stat", "--name-only", "--name-status", "--cached", "--staged", "--no-color", "--shortstat"})
        | _NO_DRIVERS,
        value_flags=frozenset(),
        required=_NO_DRIVERS,
    ),
    AllowEntry(
        program="git",
        subcommand="show",
        flags=frozenset({"--stat", "--name-only", "--no-color", "--shortstat"}) | _NO_DRIVERS,
        value_flags=frozenset(),
        required=_NO_DRIVERS,
    ),
    AllowEntry(
        program="git",
        subcommand="branch",
        flags=frozenset({"--list", "-a", "-r", "--show-current", "-v"}),
        value_flags=frozenset(),
        positional=False,
    ),
    AllowEntry(
        program="ls",
        subcommand=None,
        flags=frozenset({"-l", "-a", "-h", "-1", "-R"}),
        value_flags=frozenset(),
    ),
    AllowEntry(program="cat", subcommand=None, flags=frozenset(), value_flags=frozenset()),
    AllowEntry(
        program="rg",
        subcommand=None,
        flags=frozenset({"-n", "-i", "-S", "-l", "-c", "--no-heading", "--hidden"}),
        value_flags=frozenset({"-g", "-t"}),
    ),
)


def entry_for_prefix(words: tuple[str, ...]) -> AllowEntry | None:
    """The entry whose program and subcommand are exactly `words`, or None.

    A remembered rule is a prefix only when it has this shape. A bare program such as `git` has no such entry.
    """
    for entry in ALLOWLIST:
        head = (entry.program,) if entry.subcommand is None else (entry.program, entry.subcommand)
        if head == words:
            return entry
    return None


def _is_plain_word(word: str) -> bool:
    """True for a word of letters, digits and `_.:@+-` that does not start with a dash and has no `..`."""
    return _PLAIN_WORD.fullmatch(word) is not None and ".." not in word


def _is_combined(word: str, entry: AllowEntry) -> bool:
    """True for several listed one-letter flags in one word, such as `-la`. A value flag never combines."""
    return _SHORT_FLAGS.fullmatch(word) is not None and all(f"-{letter}" in entry.flags for letter in word[1:])


def _width(entry: AllowEntry, arguments: tuple[str, ...], index: int, *, ends_flags: bool) -> int:
    """How many words, from `index`, form one allowed argument. Zero refuses the argument."""
    word = arguments[index]
    if ends_flags or not word.startswith("-"):
        return 1 if entry.positional and _is_plain_word(word) else 0
    if word == "--":
        return 1 if entry.positional else 0
    if word in entry.flags or _is_combined(word, entry):
        return 1
    value_follows = index + 1 < len(arguments) and _is_plain_word(arguments[index + 1])
    if word in entry.value_flags and value_follows:
        return 2
    return 0


def allowed_by_entry(entry: AllowEntry, words: tuple[str, ...]) -> bool:
    """True when the whole command `words`, program first, uses only what `entry` lists.

    The program must be `entry.program`, and the subcommand must match when the entry has one. Each later word
    that starts with a dash must be a listed flag, a listed value flag followed by one plain word, or several
    listed one-letter flags together. A `--` ends flag parsing, and every word after it must be plain. Any other
    word must be plain. A flag written as `--name=value` is refused unless that exact text is listed. Every
    `required` flag of the entry must appear before any `--` (see `AllowEntry.required_if`).
    """
    if not words or words[0] != entry.program:
        return False
    arguments = words[1:]
    if entry.subcommand is not None:
        if arguments[:1] != (entry.subcommand,):
            return False
        arguments = arguments[1:]
    ends_flags = False
    seen: set[str] = set()
    index = 0
    while index < len(arguments):
        width = _width(entry, arguments, index, ends_flags=ends_flags)
        if width == 0:
            return False
        word = arguments[index]
        if not ends_flags and word.startswith("-"):
            seen.add(word)
            if _is_combined(word, entry):
                seen.update(f"-{letter}" for letter in word[1:])
        ends_flags = ends_flags or word == "--"
        index += width
    if entry.required_if and entry.required_if.isdisjoint(seen):
        return True
    return entry.required <= seen
