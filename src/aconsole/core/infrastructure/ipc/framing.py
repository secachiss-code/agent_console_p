"""NDJSON frames. One JSON object per line, at most 16 MiB."""

from __future__ import annotations

import asyncio

import msgspec

from aconsole.contracts.ipc import Envelope

MAX_MESSAGE_BYTES = 16 * 1024 * 1024


class MessageTooLargeError(Exception):
    """The frame has no newline inside the configured limit."""


# The four envelopes are tagged by `kind`, so one pass picks the type and decodes it.
_ENVELOPE: msgspec.json.Decoder[Envelope] = msgspec.json.Decoder(Envelope)


def encode_line(encoder: msgspec.json.Encoder, value: msgspec.Struct) -> bytes:
    """Compact JSON plus the NDJSON newline."""
    return encoder.encode(value) + b"\n"


def decode_envelope(line: bytes) -> Envelope:
    """Decode one frame. A trailing newline is optional."""
    payload = line[:-1] if line.endswith(b"\n") else line
    return _ENVELOPE.decode(payload)


async def read_frame(reader: asyncio.StreamReader, *, limit: int = MAX_MESSAGE_BYTES) -> bytes | None:
    """Read one NDJSON line, including the newline. None means EOF."""
    try:
        line = await reader.readuntil(b"\n")
    except asyncio.IncompleteReadError as exc:
        if exc.partial:
            message = "frame ended without a newline"
            raise MessageTooLargeError(message) from exc
        return None
    except asyncio.LimitOverrunError as exc:
        message = "frame exceeds the message limit"
        raise MessageTooLargeError(message) from exc
    if len(line) > limit + 1:
        message = "frame exceeds the message limit"
        raise MessageTooLargeError(message)
    return line
