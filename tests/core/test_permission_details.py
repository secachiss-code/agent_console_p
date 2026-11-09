"""The details used for approval must preserve the hook's literal inputs."""

import msgspec
import pytest

from aconsole.contracts.app import ApprovalView, CommandDetail, EditDetail, GenericDetail
from aconsole.core.domain.risk import RiskTag, classify_write_paths
from aconsole.core.infrastructure.http.permission_details import permission_detail


@pytest.mark.parametrize("tool", ["Edit", "MultiEdit", "Write"])
def test_edits_preserve_paths_content_and_full_arguments(tool: str) -> None:
    replacement = {"old_string": "before\n", "new_string": "after\n", "replace_all": True}
    arguments = {"file_path": "src/a b.py", **replacement, "edits": [replacement], "content": "after\n"}
    detail = permission_detail(tool, msgspec.Raw(msgspec.json.encode(arguments)))
    assert isinstance(detail, EditDetail)
    assert detail.files == ("src/a b.py",)
    assert detail.diff_preview is not None
    assert "+after" in detail.diff_preview
    if tool != "Write":
        assert "-before" in detail.diff_preview
    assert detail.input_json is not None
    assert msgspec.json.decode(detail.input_json) == arguments


def test_large_write_preview_is_bounded_but_arguments_keep_the_tail() -> None:
    content = "\n".join(f"line {number}" for number in range(500)) + "\nFINAL CONTENT"
    detail = permission_detail("Write", msgspec.Raw(msgspec.json.encode({"file_path": "a", "content": content})))
    assert isinstance(detail, EditDetail)
    assert detail.diff_preview is not None
    assert len(detail.diff_preview.splitlines()) == 200
    assert detail.diff_preview.endswith("…")
    assert detail.input_json is not None
    assert "FINAL CONTENT" in detail.input_json


def test_newline_only_edit_is_visible_in_diff() -> None:
    detail = permission_detail("Edit", msgspec.Raw(b'{"file_path":"a","old_string":"same\\n","new_string":"same"}'))
    assert isinstance(detail, EditDetail)
    assert detail.diff_preview is not None
    assert "-same" in detail.diff_preview
    assert "+same" in detail.diff_preview
    assert "\\ No newline at end of file" in detail.diff_preview


@pytest.mark.parametrize("tool", ["mcp__example__execute", "Other", "Write", "Edit", "Bash"])
def test_unknown_or_malformed_inputs_remain_visible_without_command_rules(tool: str) -> None:
    raw = b'{"command":17,"file_path":false,"arguments":{"text":"full input"}}'
    detail = permission_detail(tool, msgspec.Raw(raw))
    assert isinstance(detail, GenericDetail)
    assert msgspec.json.decode(detail.json_preview) == msgspec.json.decode(raw)


def test_mcp_command_argument_does_not_turn_into_a_shell_approval() -> None:
    raw = b'{"command":"git status","arbitrary":"must stay visible"}'
    detail = permission_detail("mcp__server__run", msgspec.Raw(raw))
    assert isinstance(detail, GenericDetail)
    assert "must stay visible" in detail.json_preview


def test_shell_keeps_the_complete_command() -> None:
    command = "echo " + "x" * 5000 + "; tail-command"
    detail = permission_detail("Bash", msgspec.Raw(msgspec.json.encode({"command": command})))
    assert isinstance(detail, CommandDetail)
    assert detail.command == command


@pytest.mark.parametrize(
    ("path", "cwd", "expected"),
    [
        ("src/a b.py", "/wt", frozenset()),
        ("src/rm -rf.py", "/wt", frozenset()),
        (".git/config", "/wt", frozenset({RiskTag.DESTRUCTIVE})),
        (".codex/config.toml", "/wt", frozenset({RiskTag.DESTRUCTIVE})),
        (".claude/settings.json", "/wt", frozenset({RiskTag.DESTRUCTIVE})),
        (".cursor/rules/main.mdc", "/wt", frozenset({RiskTag.DESTRUCTIVE})),
        ("AGENTS.md", "/wt", frozenset({RiskTag.DESTRUCTIVE})),
        ("opencode.json", "/wt", frozenset({RiskTag.DESTRUCTIVE})),
        ("../../etc/config", "/wt/src", frozenset({RiskTag.OUTSIDE_WORKTREE})),
        ("/wt2/a", "/wt", frozenset({RiskTag.OUTSIDE_WORKTREE})),
        ("a", "/outside", frozenset({RiskTag.OUTSIDE_WORKTREE})),
        (".env", "/wt", frozenset({RiskTag.SECRETS})),
    ],
)
def test_literal_edit_path_risks(path: str, cwd: str, expected: frozenset[RiskTag]) -> None:
    assert classify_write_paths((path,), cwd=cwd, worktree="/wt") == expected


def test_old_edit_details_remain_decodable() -> None:
    detail = msgspec.json.decode(b'{"type":"edit","files":["a"],"diff_preview":"+a"}', type=EditDetail)
    assert detail.input_json is None
    assert "input_json" in msgspec.json.schema(ApprovalView)["$defs"]["EditDetail"]["properties"]
