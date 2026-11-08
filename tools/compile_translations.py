"""Compile the checked-in UTF-8 PO catalogs without a system gettext dependency."""

from __future__ import annotations

import ast
import struct
from pathlib import Path


def compile_catalog(source: str) -> bytes:
    """Compile msgid/msgid_plural/msgstr entries; reject unsupported or incomplete entries."""
    messages: dict[str, str] = {}
    fields: dict[str, str] = {}
    current = ""

    def commit() -> None:
        if not fields:
            return
        key = fields["msgid"]
        if "msgid_plural" in fields:
            key += "\0" + fields["msgid_plural"]
            values = [fields[f"msgstr[{index}]"] for index in range(len(fields) - 2)]
            value = "\0".join(values)
        else:
            value = fields["msgstr"]
        if key in messages:
            message = f"duplicate message: {key}"
            raise ValueError(message)
        messages[key] = value
        fields.clear()

    for line in (*source.splitlines(), ""):
        stripped = line.strip()
        if not stripped:
            commit()
            current = ""
        elif stripped.startswith("#"):
            continue
        elif stripped.startswith('"'):
            fields[current] += _string(stripped)
        else:
            current, literal = stripped.split(" ", 1)
            if current not in {"msgid", "msgid_plural", "msgstr"} and not current.startswith("msgstr["):
                message = f"unsupported PO field: {current}"
                raise ValueError(message)
            fields[current] = _string(literal)
    keys = sorted(messages)
    count = len(keys)
    originals = [key.encode() for key in keys]
    translations = [messages[key].encode() for key in keys]
    offset = 28 + 16 * count
    offsets: list[int] = []
    blob = bytearray()
    for value in (*originals, *translations):
        offsets.extend((len(value), offset + len(blob)))
        blob.extend(value + b"\0")
    return struct.pack("<7I", 0x950412DE, 0, count, 28, 28 + 8 * count, 0, 0) + struct.pack(
        f"<{len(offsets)}I", *offsets,
    ) + blob


def _string(literal: str) -> str:
    result = ast.literal_eval(literal)
    if not isinstance(result, str):
        message = "PO values must be strings"
        raise TypeError(message)
    return result


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1] / "src/aconsole/ui/locales"
    for path in sorted(root.glob("*/LC_MESSAGES/aconsole.po")):
        path.with_suffix(".mo").write_bytes(compile_catalog(path.read_text()))
