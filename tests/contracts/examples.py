"""Build one valid instance of every msgspec struct."""

from __future__ import annotations

import types
import typing
from datetime import UTC, datetime
from enum import StrEnum
from typing import NewType

import msgspec
from msgspec import Meta

from aconsole.contracts.generate import struct_types

ULID = "01JB2Z00000000000000000001"
AT = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)


def samples() -> dict[str, msgspec.Struct]:
    built: dict[str, msgspec.Struct] = {}
    for struct in struct_types():
        built[struct.__name__] = instantiate(struct)
    return built


def instantiate(struct: type[msgspec.Struct]) -> msgspec.Struct:
    hints = typing.get_type_hints(struct, include_extras=True)
    kwargs: dict[str, object] = {}
    for field in msgspec.structs.fields(struct):
        if not field.required:
            continue
        kwargs[field.name] = value_for(hints[field.name])
    return struct(**kwargs)


def value_for(annotation: object) -> object:
    origin = typing.get_origin(annotation)
    if origin is typing.Annotated:
        return _annotated(annotation)
    if origin is typing.Literal:
        return typing.get_args(annotation)[0]
    if origin in {types.UnionType, typing.Union}:
        choices = [item for item in typing.get_args(annotation) if item is not type(None)]
        return value_for(choices[0])
    collected = _collection(origin, annotation)
    if collected is not _MISSING:
        return collected
    return _scalar(annotation)


_MISSING = object()


def _annotated(annotation: object) -> object:
    args = typing.get_args(annotation)
    meta = next((item for item in args[1:] if isinstance(item, Meta)), None)
    if isinstance(meta, Meta) and meta.pattern is not None:
        return ULID
    if isinstance(meta, Meta) and meta.min_length is not None:
        return "x" * meta.min_length
    if isinstance(meta, Meta) and meta.ge is not None and not isinstance(meta.ge, float):
        return int(meta.ge)
    return value_for(args[0])


def _collection(origin: object, annotation: object) -> object:
    if origin is tuple:
        args = typing.get_args(annotation)
        if args and args[-1] is Ellipsis:
            return ()
        return tuple(value_for(item) for item in args)
    if origin in {frozenset, set}:
        return frozenset()
    if origin is dict:
        return {}
    if origin is list:
        return []
    return _MISSING


def _scalar(annotation: object) -> object:
    if isinstance(annotation, type) and issubclass(annotation, msgspec.Struct):
        return instantiate(annotation)
    if isinstance(annotation, type) and issubclass(annotation, StrEnum):
        return next(iter(annotation))
    scalars: dict[object, object] = {
        str: "x",
        int: 1,
        float: 1.0,
        bool: True,
        datetime: AT,
        msgspec.Raw: msgspec.Raw(b"{}"),
    }
    if annotation in scalars:
        return scalars[annotation]
    if isinstance(annotation, NewType):
        return value_for(annotation.__supertype__)
    message = f"no sample for {annotation!r}"
    raise TypeError(message)
