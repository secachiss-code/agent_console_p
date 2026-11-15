"""Small gate summaries suitable for agent handoff."""

from collections.abc import Sequence

from aconsole.core.application.ports.gate_runner import GateResult


def compact_verdict(results: Sequence[GateResult]) -> str:
    failed = [result for result in results if result.returncode != 0]
    summary = f"{len(results) - len(failed)} passed, {len(failed)} failed"
    for result in failed:
        lines = [line.strip() for line in result.output.splitlines() if line.strip()]
        error = next((line for line in lines if "error" in line.casefold()), lines[0] if lines else "no output")
        detail = "timeout" if result.timed_out else error
        summary += f"\n{result.argv[0]}: {detail}"
    return summary[:500]
