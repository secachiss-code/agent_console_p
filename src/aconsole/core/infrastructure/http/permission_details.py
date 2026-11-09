"""Present hook inputs without reading files from the host's filesystem."""

from difflib import unified_diff
from itertools import islice

import msgspec

from aconsole.contracts.app import ApprovalDetail, CommandDetail, EditDetail, GenericDetail

_DIFF_LINES = 200
_SHELL_TOOLS = frozenset({"Bash", "bash", "shell", "shell_command"})


class _Command(msgspec.Struct, frozen=True, kw_only=True):
    command: str


class _Replacement(msgspec.Struct, frozen=True, kw_only=True):
    old_string: str
    new_string: str


class _Edit(msgspec.Struct, frozen=True, kw_only=True):
    file_path: str
    old_string: str
    new_string: str


class _MultiEdit(msgspec.Struct, frozen=True, kw_only=True):
    file_path: str
    edits: tuple[_Replacement, ...]


class _Write(msgspec.Struct, frozen=True, kw_only=True):
    file_path: str
    content: str


def _diff(before: str, after: str, path: str) -> str:
    # These are the supplied replacement fragments, not a reconstruction of the on-disk file.
    lines = unified_diff(before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile=path, tofile=path)
    # A replacement of just the trailing newline is still a change worth approving.
    return "".join(line if line.endswith("\n") else line + "\n\\ No newline at end of file\n" for line in lines)


def permission_detail(tool: str, raw: msgspec.Raw) -> ApprovalDetail:
    """Known edits get a bounded preview and complete arguments; unknown inputs stay fully readable."""
    full = msgspec.json.format(bytes(raw), indent=2).decode()
    try:
        if tool in _SHELL_TOOLS:
            return CommandDetail(command=msgspec.json.decode(raw, type=_Command).command, args_preview=None)
        if tool == "Write":
            write = msgspec.json.decode(raw, type=_Write)
            path = write.file_path
            # Write replaces the file. Do not invent an old version by reading a same-named host file.
            preview = "\n".join((f"+++ {path}", *("+" + line for line in write.content.splitlines())))
        elif tool == "Edit":
            edit = msgspec.json.decode(raw, type=_Edit)
            path = edit.file_path
            preview = _diff(edit.old_string, edit.new_string, path)
        elif tool == "MultiEdit":
            multi = msgspec.json.decode(raw, type=_MultiEdit)
            path = multi.file_path
            preview = "\n".join(_diff(edit.old_string, edit.new_string, path) for edit in multi.edits)
        else:
            return GenericDetail(json_preview=full)
    except msgspec.DecodeError:
        return GenericDetail(json_preview=full)
    lines = list(islice(preview.splitlines(), _DIFF_LINES + 1))
    if len(lines) > _DIFF_LINES:
        lines = [*lines[:_DIFF_LINES - 1], "…"]
    return EditDetail(files=(path,), diff_preview="\n".join(lines), input_json=full)
