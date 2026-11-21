"""Daily usage integration and snapshot compatibility."""

import json
from datetime import UTC, datetime
lazy from pathlib import Path

import msgspec

from aconsole.contracts.app import AppSnapshot
from aconsole.contracts.common import AgentKind
from aconsole.core.bootstrap import _read_usage  # pyright: ignore[reportPrivateUsage]
from aconsole.core.infrastructure.agents.usage_log import UsageLog, claude_events, codex_events, usage_overview
from tests.contracts.examples import samples


def test_daily_usage_deduplicates_across_logs_and_distinguishes_zero_from_missing(tmp_path: Path) -> None:
    claude = tmp_path / "claude"
    codex = tmp_path / "codex"
    claude.mkdir()
    codex.mkdir()

    def reply(identity: str, at: str, output: int) -> str:
        return json.dumps(
            {
                "type": "assistant",
                "timestamp": at,
                "requestId": identity,
                "message": {"id": identity, "model": "claude-test", "usage": {"output_tokens": output}},
            }
        )

    _ = (claude / "session.jsonl").write_text(
        "\n".join(
            (
                reply("outside", "2026-10-05T12:00:00Z", 99),
                reply("duplicate", "2026-10-15T23:59:00Z", 4),
                reply("duplicate", "2026-10-16T01:01:00+01:00", 7),
                reply("zero", "2026-10-18T10:00:00Z", 0),
            )
        ),
        encoding="utf-8",
    )
    records = (
        {"type": "session_meta", "payload": {"id": "session"}},
        {"type": "turn_context", "payload": {"model": "codex-test"}},
        {
            "type": "event_msg",
            "timestamp": "2026-10-16T03:00:00Z",
            "payload": {
                "type": "token_count",
                "info": {
                    "total_token_usage": {"total_tokens": 12},
                    "last_token_usage": {"input_tokens": 8, "cached_input_tokens": 3, "output_tokens": 4},
                },
            },
        },
    )
    for filename in ("original.jsonl", "copy.jsonl"):
        _ = (codex / filename).write_text("\n".join(json.dumps(record) for record in records), encoding="utf-8")

    now = datetime(2026, 11, 5, 12, tzinfo=UTC)
    claude_history, _ = _read_usage(UsageLog(lambda: claude, claude_events), AgentKind.CLAUDE, now)
    codex_history, _ = _read_usage(UsageLog(lambda: codex, codex_events), AgentKind.CODEX, now)
    assert claude_history.start_day == datetime(2026, 10, 7, tzinfo=UTC)
    assert claude_history.end_day == datetime(2026, 11, 5, tzinfo=UTC)
    assert [row.day for row in claude_history.days] == [
        datetime(2026, 10, 16, tzinfo=UTC),
        datetime(2026, 10, 18, tzinfo=UTC),
    ]
    assert claude_history.days[1].models[0].output_tokens == 0
    assert claude_history.totals[0].output_tokens == 7
    (model,) = codex_history.totals
    assert (model.input_tokens, model.cache_read_tokens, model.output_tokens) == (5, 3, 4)
    assert usage_overview((claude_history, codex_history)).active_days == 1


def test_snapshot_without_usage_overview_still_decodes() -> None:
    snapshot = msgspec.json.decode(msgspec.json.encode(samples()["AppSnapshot"]), type=dict[str, object])
    del snapshot["usage_overview"]
    assert msgspec.json.decode(msgspec.json.encode(snapshot), type=AppSnapshot).usage_overview is None
