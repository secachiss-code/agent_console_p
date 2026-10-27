"""ULID strings. 48 bits of milliseconds and 80 bits of randomness."""

from collections.abc import Callable

from aconsole.core.domain.clock import Clock

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_TIME_CHARS = 10
_RANDOM_BYTES = 10
_RANDOM_CHARS = 16
_MASK = 0x1F


def new_ulid(clock: Clock, random_bytes: Callable[[int], bytes]) -> str:
    millis = int(clock.now().timestamp() * 1000)
    if millis < 0 or millis >= 2**48:
        message = "ULID time is outside the 48-bit range"
        raise ValueError(message)
    entropy = random_bytes(_RANDOM_BYTES)
    if len(entropy) != _RANDOM_BYTES:
        message = "ULID randomness must be 10 bytes"
        raise ValueError(message)
    return _encode(millis, _TIME_CHARS * 5) + _encode(int.from_bytes(entropy), _RANDOM_CHARS * 5)


def _encode(value: int, bits: int) -> str:
    chars = [_ALPHABET[(value >> shift) & _MASK] for shift in range(bits - 5, -1, -5)]
    return "".join(chars)
