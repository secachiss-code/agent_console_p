"""Shared msgspec contracts for the UI process and the core."""

from aconsole.contracts.errors import ERRORS
from aconsole.contracts.methods import METHODS

API_VERSION = "1.1.0"

__all__ = ["API_VERSION", "ERRORS", "METHODS"]
