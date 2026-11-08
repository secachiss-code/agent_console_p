"""Tagged observation data inside CheckResult.detail; presentation lives in the UI."""

from typing import Literal

import msgspec


# CheckResult.detail remains a string for wire compatibility. Structured setup
# observations use this tagged JSON payload so paths are never delimiter-parsed.
class SetupFact(msgspec.Struct, frozen=True, kw_only=True):
    code: str
    values: tuple[str, ...] = ()


class SetupDetail(msgspec.Struct, frozen=True, kw_only=True):
    format: Literal["setup_detail.v1"]
    facts: tuple[SetupFact, ...]
