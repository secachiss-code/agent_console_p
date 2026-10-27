"""Shell-command risk tags. The strings match the contract RiskTag."""

import os
import re
import shlex
from enum import StrEnum
from pathlib import Path


class RiskTag(StrEnum):
    DESTRUCTIVE = "destructive"
    OUTSIDE_WORKTREE = "outside_worktree"
    NETWORK = "network"
    PUBLISH = "publish"
    SECRETS = "secrets"
    PRIVILEGE = "privilege"


_PRIVILEGE = frozenset({"sudo", "doas", "su", "chmod", "chown", "chgrp", "mount", "umount", "systemctl", "pkexec"})
_DESTRUCTIVE_BIN = frozenset({"rm", "rmdir", "shred", "unlink", "wipefs", "mkfs", "dd", "dropdb", "truncate", "eval"})
_NETWORK_BIN = frozenset({"curl", "wget", "ssh", "scp", "nc", "ncat", "ping"})
_INSTALLERS = frozenset({"pip", "npm", "pnpm", "yarn", "bun", "uv", "cargo", "go", "gem"})
_INSTALL_VERBS = frozenset(
    {"install", "i", "add", "exec", "dlx", "download", "fetch", "get", "mod", "x", "run", "update", "publish", "tidy"}
)
_PACKAGE_MANAGERS = frozenset({"apt-get", "apt", "dnf", "yum", "apk"})
_PACKAGE_VERBS = frozenset({"install", "update", "upgrade", "add"})
_COPY_TOOLS = frozenset({"rsync", "scp"})
# Names are matched as substrings of each word, so `.pem` also covers `cert.pem.bak`.
_SECRET_NAMES = (
    ".env",
    "id_rsa",
    "id_ecdsa",
    "id_ed25519",
    ".pem",
    ".key",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "credentials",
    "secret",
)
_SHELLS = frozenset({"sh", "bash", "zsh", "dash", "ksh"})
# Each interpreter maps to the option letters that take inline code: `python -c`, `perl -E`, `node -p`, `php -r`.
_INTERPRETERS = {"python": "c", "perl": "eE", "node": "ep", "ruby": "e", "php": "r", "lua": "e"}
# `at` and `batch` run the text piped or redirected into them.
_RUNNERS = _SHELLS | frozenset(_INTERPRETERS) | {"deno", "at", "batch"}
_LONG_INLINE = frozenset({"--eval", "--print"})
# Interpreter options whose next word is their value, so that word is not a script or module.
_TAKES_VALUE = frozenset({"-W", "-X", "-I", "-M", "-c", "-r", "--require"})
_CONTAINER_TOOLS = frozenset({"docker", "podman"})
# Programs that run the text given as their arguments. An argument with whitespace or punctuation is a command.
_EXECUTORS = frozenset(
    {
        "su",
        "runuser",
        "sudo",
        "doas",
        "watch",
        "parallel",
        "at",
        "ssh",
        "xargs",
        "nohup",
        "timeout",
        "nice",
        "stdbuf",
        "time",
        "env",
        "busybox",
        "nsenter",
        "unshare",
        "chroot",
        "setsid",
        "strace",
        "ionice",
        "flock",
        "command",
        "exec",
    }
)
# Words that may precede a program. `at` is left out: it runs the text piped into it, so it stays the pipe target.
_WRAPPERS = _EXECUTORS - {"at"}
_SEPARATORS = frozenset({"|", "|&", "||", "&", "&&", ";"})
_PIPES = frozenset({"|", "|&"})
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
_FIND_EXEC = frozenset({"-exec", "-execdir", "-ok", "-okdir"})
_FIND_END = frozenset({";", "+"})
_PUNCTUATION = "`$(){};&|<>"
_OPERATOR_CHARS = "|&;<>"
# awk and sed run the code in their arguments, so a word with a space or these marks is code.
_SCRIPT_TOOLS = frozenset({"awk", "gawk", "mawk", "sed", "gsed"})
_CODE_MARKS = "({"
_REDIRECT_CHARS = frozenset("<>&")
_TRUNCATING = frozenset({">", ">|", "&>"})
_CHECKOUT_BRANCH_FLAGS = frozenset({"-b", "-B", "--orphan"})
_CHECKOUT_FORCE_FLAGS = frozenset({"-f", "--force"})
_CONFIG_WRITE_FLAGS = frozenset({"--add", "--unset", "--unset-all", "--replace-all", "--global", "--system"})
_PIP_CONFIG_WRITES = frozenset({"set", "unset", "edit"})
_MAX_DEPTH = 3


def _split(text: str) -> list[str] | None:
    """Shell words of `text`, or None when quoting is unbalanced. The caller then fails closed.

    Operators such as `|`, `&&` and `<<<` become their own words even without spaces, so `x|sh` shows its pipe.
    """
    lexer = shlex.shlex(text, posix=True, punctuation_chars=_OPERATOR_CHARS)
    lexer.whitespace_split = True
    lexer.commenters = ""
    try:
        return list(lexer)
    except ValueError:
        return None


def _name(token: str) -> str:
    """The program a word could run: its basename, without shell punctuation around it."""
    return Path(token.strip(_PUNCTUATION)).name


def _base(name: str) -> str:
    """Version suffixes name the same program, so `python3.12` is `python`."""
    return name.rstrip("0123456789.")


def _has(names: list[str], *needles: str) -> bool:
    return all(needle in names for needle in needles)


def _arguments(tokens: list[str], index: int) -> list[str]:
    """The words after `tokens[index]` up to the next separator, so that one command's arguments stay apart."""
    end = next((pos for pos in range(index + 1, len(tokens)) if tokens[pos] in _SEPARATORS), len(tokens))
    return tokens[index + 1 : end]


def _positional(words: list[str]) -> list[str]:
    return [word for word in words if not word.startswith("-")]


def _has_mark(word: str, marks: str) -> bool:
    """True when `word` holds whitespace or one of the characters in `marks`."""
    return any(char.isspace() or char in marks for char in word)


def _skip(word: str) -> bool:
    """Words that may precede a program: assignments, options, numbers, and wrappers such as `sudo` or `timeout`."""
    return _ASSIGNMENT.match(word) is not None or word.startswith("-") or word.isdigit() or _name(word) in _WRAPPERS


def _starts_command(tokens: list[str], index: int) -> bool:
    """True when only assignments, options, numbers and wrappers come before `tokens[index]` in its command."""
    position = index
    while position > 0 and tokens[position - 1] not in _SEPARATORS:
        position -= 1
        if not _skip(tokens[position]):
            return False
    return True


def _destructive(names: list[str]) -> bool:
    if any(name in _DESTRUCTIVE_BIN or name.startswith("mkfs.") for name in names):
        return True
    if _has(names, "git", "reset", "--hard") or _has(names, "git", "clean"):
        return True
    if _has(names, "git", "push") and any(flag in names for flag in ("--force", "-f")):
        return True
    return _has(names, "kubectl", "delete") or _has(names, "terraform", "destroy")


def _git_discards(tokens: list[str], names: list[str]) -> bool:
    """Commands that throw away uncommitted work or stashes: `git checkout` of a path, `--` or `-f`, and stash drop.

    A lone word after `git checkout` is taken as a branch, unless it has a dot, which marks a file name.
    """
    if _has(names, "git", "stash") and ("drop" in names or "clear" in names):
        return True
    if not _has(names, "git", "checkout"):
        return False
    words = _arguments(tokens, names.index("checkout"))
    if "--" in words or any(word in _CHECKOUT_FORCE_FLAGS for word in words):
        return True
    if any(word in _CHECKOUT_BRANCH_FLAGS for word in words):
        return False
    paths = _positional(words)
    return len(paths) > 1 or any("." in word for word in paths)


def _config_writes(tokens: list[str], names: list[str]) -> bool:
    """Settings writes: `git config` that sets, unsets or names a scope, and `pip config set|unset|edit`."""
    if _has(names, "git", "config"):
        words = _arguments(tokens, names.index("config"))
        if any(word in _CONFIG_WRITE_FLAGS for word in words) or len(_positional(words)) > 1:
            return True
    for index, name in enumerate(names):
        if _base(name) == "pip":
            words = _positional(_arguments(tokens, index))
            if len(words) > 1 and words[0] == "config" and words[1] in _PIP_CONFIG_WRITES:
                return True
    return False


def _truncating_segment(segment: list[str]) -> bool:
    """A truncating `>` whose command is empty (`> x`, `: > x`) or reads /dev/null (`cat /dev/null > x`)."""
    truncates = False
    runs_program = False
    reads_null = False
    target_next = False
    for word in segment:
        if target_next:
            target_next = False
        elif _is_redirect(word):
            truncates = truncates or word in _TRUNCATING
            target_next = True
        elif word == "/dev/null":
            reads_null = True
        elif word != ":" and not _skip(word):
            runs_program = True
    return truncates and (reads_null or not runs_program)


def _is_redirect(word: str) -> bool:
    return word != "" and frozenset(word) <= _REDIRECT_CHARS


def _segments(tokens: list[str]) -> list[list[str]]:
    """The words of each command, split at the separators."""
    segments: list[list[str]] = [[]]
    for token in tokens:
        if token in _SEPARATORS:
            segments.append([])
        else:
            segments[-1].append(token)
    return segments


def _truncates(tokens: list[str]) -> bool:
    """`> x` and `: > x` empty a file, and so does `cat /dev/null > x`."""
    return any(_truncating_segment(segment) for segment in _segments(tokens))


def _code_arguments(tokens: list[str], names: list[str]) -> bool:
    """awk and sed run code from their arguments (`system()`, the `e` command), so such an argument is destructive."""
    for index, name in enumerate(names):
        if _base(name) in _SCRIPT_TOOLS and any(_has_mark(word, _CODE_MARKS) for word in _arguments(tokens, index)):
            return True
    return False


def _env_dump(tokens: list[str], names: list[str]) -> bool:
    """`env` without a command prints the whole environment."""
    for index, name in enumerate(names):
        if name != "env" or not _starts_command(tokens, index):
            continue
        if all(word.startswith("-") or _ASSIGNMENT.match(word) is not None for word in _arguments(tokens, index)):
            return True
    return False


def _network(names: list[str]) -> bool:
    if any(name in _NETWORK_BIN for name in names):
        return True
    if _has(names, "git") and any(name in {"clone", "fetch", "pull", "push"} for name in names):
        return True
    if _has(names, "git", "remote", "update") or _has(names, "git", "submodule", "update"):
        return True
    if _has(names, "pip", "install") or _has(names, "npm", "install") or _has(names, "npm", "publish"):
        return True
    if _has(names, "poetry", "publish") or _has(names, "twine", "upload") or _has(names, "cargo", "publish"):
        return True
    return _has(names, "docker", "pull") or _has(names, "docker", "push") or _has(names, "gh")


def _installs(tokens: list[str], names: list[str]) -> bool:
    """Package tools that fetch code: `npx` always, the others when their first argument is an install verb.

    `uv pip install` takes its verb after `pip`. An option that takes a value, as in `npm --prefix x install`,
    is not skipped, so its value counts as the verb.
    """
    for index, name in enumerate(names):
        tool = _base(name)
        if tool == "npx":
            return True
        words = _positional(_arguments(tokens, index))
        if tool == "uv" and words[:1] == ["pip"]:
            words = words[1:]
        verb = words[0] if words else ""
        if (tool in _INSTALLERS and verb in _INSTALL_VERBS) or (tool in _PACKAGE_MANAGERS and verb in _PACKAGE_VERBS):
            return True
    return False


def _host_operand(word: str) -> bool:
    """A `host:path` operand. A colon after a slash, as in `./a:b`, marks a local path."""
    return not word.startswith("-") and ":" in word and "/" not in word.partition(":")[0]


def _remote_copy(tokens: list[str], names: list[str]) -> bool:
    for index, name in enumerate(names):
        if _base(name) in _COPY_TOOLS and any(_host_operand(word) for word in _arguments(tokens, index)):
            return True
    return False


def _publish(names: list[str]) -> bool:
    if _has(names, "npm", "publish") or _has(names, "poetry", "publish") or _has(names, "twine", "upload"):
        return True
    if _has(names, "cargo", "publish") or _has(names, "docker", "push") or _has(names, "gem", "push"):
        return True
    return _has(names, "gh", "release")


def _secrets(tokens: list[str], names: list[str]) -> bool:
    if "printenv" in names or _env_dump(tokens, names):
        return True
    return any(any(name in token for name in _SECRET_NAMES) for token in tokens)


def _outside(token: str, *, cwd: str, worktree: str) -> bool:
    if token.startswith("~"):
        return True
    candidate = token.split("=", 1)[1] if "=" in token else token
    path = Path(candidate)
    if not path.is_absolute() and ".." not in path.parts:
        return False
    joined = candidate if path.is_absolute() else str(Path(cwd) / candidate)
    resolved = Path(os.path.normpath(joined))
    root = Path(os.path.normpath(worktree))
    try:
        _ = resolved.relative_to(root)
    except ValueError:
        return True
    return False


def _mount_source(spec: str) -> list[str]:
    """The `src` or `source` values of a `--mount` specification such as `type=bind,src=/,dst=/host`."""
    pairs = [item.partition("=") for item in spec.split(",")]
    return [value for key, _, value in pairs if key in {"src", "source"}]


def _mount_sources(tokens: list[str]) -> list[str]:
    """Host paths named by the `-v`, `--volume` and `--mount` options."""
    sources: list[str] = []
    for index, word in enumerate(tokens):
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        if word in {"-v", "--volume"}:
            sources.append(following.split(":", 1)[0])
        elif word.startswith("--volume="):
            sources.append(word.partition("=")[2].split(":", 1)[0])
        elif word.startswith("-v") and not word.startswith("--"):
            sources.append(word[2:].split(":", 1)[0])
        elif word == "--mount":
            sources.extend(_mount_source(following))
        elif word.startswith("--mount="):
            sources.extend(_mount_source(word.partition("=")[2]))
    return sources


def _host_mount(tokens: list[str], names: list[str]) -> bool:
    """A docker or podman bind mount or volume whose source is an absolute or home path lies outside the worktree."""
    if not any(_base(name) in _CONTAINER_TOOLS for name in names):
        return False
    return any(source.startswith(("/", "~")) for source in _mount_sources(tokens))


def _compose(tokens: list[str], names: list[str]) -> bool:
    """`docker compose`, `podman compose` and `docker-compose` read compose files, which can mount any host path."""
    for index, name in enumerate(names):
        tool = _base(name)
        if tool == "docker-compose":
            return True
        if tool in _CONTAINER_TOOLS and _positional(_arguments(tokens, index))[:1] == ["compose"]:
            return True
    return False


def _find_destructive(tokens: list[str], names: list[str]) -> bool:
    """`find -delete` removes files, and `find -exec` runs a command for every match."""
    return "find" in names and any(word == "-delete" or word in _FIND_EXEC for word in tokens)


def _recursive_root_change(tokens: list[str], names: list[str]) -> bool:
    """A `chmod` or `chown` that recurses, or that names the root directory, changes far more than one file."""
    for index, name in enumerate(names):
        if name not in {"chmod", "chown"}:
            continue
        for word in tokens[index + 1 :]:
            if word == "--recursive" or (word.startswith("-") and not word.startswith("--") and "R" in word):
                return True
            if word.startswith("/") and word.strip("/") == "":
                return True
    return False


def _inline_flag(args: list[str], flags: str) -> bool:
    """True when the interpreter's own options, before its script or module, include an inline-code flag."""
    position = 0
    while position < len(args):
        word = args[position]
        if word.split("=", 1)[0] in _LONG_INLINE or (
            word.startswith("-") and not word.startswith("--") and any(flag in word for flag in flags)
        ):
            return True
        if not word.startswith("-"):
            return False
        takes_value = word in _TAKES_VALUE and position + 1 < len(args) and not args[position + 1].startswith("-")
        position += 2 if takes_value else 1
    return False


def _inline_code(tokens: list[str], names: list[str]) -> bool:
    """True when an interpreter gets its program inline (`python -c`, `perl -e`, `node -p`, `deno eval`).

    Only the interpreter's own options count, so `python -m pytest -c x.ini` is not inline code.
    Such code cannot be inspected, so the caller treats it as destructive.
    """
    for index, name in enumerate(names):
        tool = _base(name)
        if tool == "deno" and tokens[index + 1 : index + 2] == ["eval"]:
            return True
        flags = _INTERPRETERS.get(tool)
        if flags is not None and _inline_flag(tokens[index + 1 :], flags):
            return True
    return False


def _pipe_targets(tokens: list[str]) -> list[str]:
    """The program that each pipe feeds: the first word after it that is not a wrapper, option or assignment."""
    targets: list[str] = []
    for index, token in enumerate(tokens):
        if token not in _PIPES:
            continue
        for word in tokens[index + 1 :]:
            if word in _SEPARATORS:
                break
            if not _skip(word):
                targets.append(_name(word))
                break
    return targets


def _fed_to_runner(tokens: list[str], names: list[str]) -> bool:
    """Text piped, or given as a here-string or here-document, to a shell, interpreter or `at` runs unseen."""
    if any(token.startswith("<<") for token in tokens) and any(_base(name) in _RUNNERS for name in names):
        return True
    return any(_base(name) in _RUNNERS for name in _pipe_targets(tokens))


def _dynamic_program(tokens: list[str]) -> bool:
    """A program word with `$` cannot be resolved statically, as in `rm$IFS-rf` or `$'r\\155'`, so it fails closed."""
    at_start = True
    for token in tokens:
        if token in _SEPARATORS:
            at_start = True
        elif at_start and _ASSIGNMENT.match(token) is None:
            if "$" in token:
                return True
            if not _skip(token):
                at_start = False
    return False


def _shell_script(tokens: list[str], start: int) -> str | None:
    """The script of the first `-c` flag cluster at or after `start`. A cluster such as `-lc` contains `c`."""
    for position in range(start, len(tokens)):
        token = tokens[position]
        if token.startswith("-") and not token.startswith("--") and "c" in token:
            words = [word for word in tokens[position + 1 :] if word != "--"]
            return words[0] if words else None
    return None


def _env_strings(tokens: list[str]) -> list[str]:
    """The strings that `env -S` or `--split-string` split into a command."""
    found: list[str] = []
    for index, token in enumerate(tokens):
        following = tokens[index + 1] if index + 1 < len(tokens) else ""
        if token.startswith("--split-string"):
            found.append(token.partition("=")[2] if "=" in token else following)
        elif token.startswith("-") and not token.startswith("--") and "S" in token:
            found.append(token[token.index("S") + 1 :] or following)
    return found


def _scripts(tokens: list[str], names: list[str]) -> list[str]:
    """Command text that a shell (`sh -c`), `env -S`, or an executor wrapper runs.

    The scan does not depend on position, so a wrapper or an option the code does not know cannot hide the shell.
    An executor's argument counts as command text only when it holds whitespace or shell punctuation.
    """
    found: list[str] = []
    for index, name in enumerate(names):
        if _base(name) in _SHELLS:
            script = _shell_script(tokens, index + 1)
            if script is not None:
                found.append(script)
        if name in _EXECUTORS:
            found.extend(word for word in _arguments(tokens, index) if _has_mark(word, _PUNCTUATION))
    if "env" in names:
        found.extend(_env_strings(tokens))
    return found


def _exec_payloads(tokens: list[str], names: list[str]) -> list[list[str]]:
    """The argument lists that `find -exec` runs, each up to its `;` or `+`."""
    if "find" not in names:
        return []
    payloads: list[list[str]] = []
    for index, token in enumerate(tokens):
        if token in _FIND_EXEC:
            end = next((pos for pos in range(index + 1, len(tokens)) if tokens[pos] in _FIND_END), len(tokens))
            payloads.append(tokens[index + 1 : end])
    return payloads


def _nested(tokens: list[str], names: list[str], *, cwd: str, worktree: str, depth: int) -> frozenset[RiskTag]:
    tags: set[RiskTag] = set()
    for text in _scripts(tokens, names):
        inner = _split(text)
        if inner is None:
            tags.update(RiskTag)
        else:
            tags |= _classify_tokens(inner, cwd=cwd, worktree=worktree, depth=depth + 1)
    for payload in _exec_payloads(tokens, names):
        tags |= _classify_tokens(payload, cwd=cwd, worktree=worktree, depth=depth + 1)
    return frozenset(tags)


def _destroys(tokens: list[str], names: list[str]) -> bool:
    return (
        _destructive(names)
        or _find_destructive(tokens, names)
        or _inline_code(tokens, names)
        or _fed_to_runner(tokens, names)
        or _dynamic_program(tokens)
        or _recursive_root_change(tokens, names)
        or _git_discards(tokens, names)
        or _config_writes(tokens, names)
        or _truncates(tokens)
        or _code_arguments(tokens, names)
    )


def _classify_tokens(tokens: list[str], *, cwd: str, worktree: str, depth: int) -> frozenset[RiskTag]:
    if depth > _MAX_DEPTH:
        return frozenset(RiskTag)
    names = [_name(token) for token in tokens]
    tags: set[RiskTag] = set()
    if _destroys(tokens, names):
        tags.add(RiskTag.DESTRUCTIVE)
    if _network(names) or _installs(tokens, names) or _remote_copy(tokens, names):
        tags.add(RiskTag.NETWORK)
    if _publish(names):
        tags.add(RiskTag.PUBLISH)
    if _secrets(tokens, names):
        tags.add(RiskTag.SECRETS)
    if any(name in _PRIVILEGE for name in names):
        tags.add(RiskTag.PRIVILEGE)
    if any(_outside(token, cwd=cwd, worktree=worktree) for token in tokens) or _host_mount(tokens, names):
        tags.add(RiskTag.OUTSIDE_WORKTREE)
    if _compose(tokens, names):
        tags.add(RiskTag.OUTSIDE_WORKTREE)
    return frozenset(tags) | _nested(tokens, names, cwd=cwd, worktree=worktree, depth=depth)


def classify(command: str, *, cwd: str, worktree: str) -> frozenset[RiskTag]:
    """Risk tags for a shell command, including the commands that wrappers and shells run.

    Wrappers (`env`, `timeout`, `sudo`, `nohup`, `xargs`, `VAR=value`, and so on) do not hide the program,
    because every program name is checked at any position. `sh|bash|zsh|dash|ksh` with a `-c` flag cluster,
    and `env -S`, have their script classified recursively, and so do the arguments of executor wrappers
    (`su -c`, `ssh host`, `watch`) that hold whitespace or punctuation. `find -delete` and `find -exec` are
    destructive, and their payload is classified too. Inline code, pipes or here-strings into a shell,
    interpreter or `at`, `$` in a program word, code arguments of awk or sed, and truncating redirects are
    destructive. Unbalanced quotes and nesting deeper than `_MAX_DEPTH` fail closed with every tag.
    """
    tokens = _split(command)
    if tokens is None:
        return frozenset(RiskTag)
    return _classify_tokens(tokens, cwd=cwd, worktree=worktree, depth=0)
