"""Shared msgspec contracts for the UI process and the core."""

from aconsole.contracts.errors import ERRORS
from aconsole.contracts.methods import METHODS
from aconsole.contracts.pipeline import HandoffEnvelope, StateBlock

API_VERSION = "1.6.0"

__all__ = ["API_VERSION", "ERRORS", "METHODS", "HandoffEnvelope", "StateBlock"]
