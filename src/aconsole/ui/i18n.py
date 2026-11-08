"""Shared gettext lookup and presentation formats. Never mutate the process locale."""

from __future__ import annotations

import gettext
import locale
from datetime import datetime
from functools import lru_cache
from importlib.resources import files

import msgspec

from aconsole.contracts.app import CheckResult
from aconsole.contracts.setup_detail import SetupDetail


def language() -> str:
    """The UI follows the desktop locale; unsupported languages use English."""
    name = locale.getlocale()[0] or "en"
    return "ru" if name.lower().startswith("ru") else "en"


@lru_cache(maxsize=2)
def _catalog(lang: str) -> gettext.GNUTranslations:
    resource = files("aconsole.ui").joinpath("locales", lang, "LC_MESSAGES", "aconsole.mo")
    with resource.open("rb") as stream:
        return gettext.GNUTranslations(stream)


def _(key: str, count: int | None = None, *, lang: str | None = None) -> str:
    selected = language() if lang is None else "ru" if lang.lower().startswith("ru") else "en"
    catalog = _catalog(selected)
    text = catalog.gettext(key) if count is None else catalog.ngettext(key, key + ".plural", count)
    if text in {key, key + ".plural"}:
        raise KeyError(key)
    return text


def known(key: str) -> bool:
    return _catalog("en").gettext(key) != key


def core_message(text: str) -> str:
    """Translate a known core message ID; preserve older core and external diagnostic text."""
    return _(text) if known(text) else text


def format_number(value: float, decimals: int = 0, *, grouping: bool = True) -> str:
    text = format(value, f"{',' if grouping else ''}.{decimals}f")
    return text.translate(str.maketrans({",": "\N{NO-BREAK SPACE}", ".": ","})) if language() == "ru" else text


def format_date(value: datetime, *, year: bool = False) -> str:
    """Local timezone, Russian day-first or unambiguous English ISO order."""
    pattern = ("%d.%m.%Y" if year else "%d.%m") if language() == "ru" else ("%Y-%m-%d" if year else "%m-%d")
    return value.astimezone().strftime(pattern + " %H:%M")



def setup_detail(text: str | None) -> str:
    """Render tagged setup observations without parsing paths or formatting user content."""
    if not text:
        return ""
    try:
        detail = msgspec.json.decode(text, type=SetupDetail)
    except msgspec.DecodeError:
        return core_message(text)
    return "\n".join(_(fact.code).format(*fact.values) if known(fact.code) else fact.code for fact in detail.facts)


def setup_check_reason(check: CheckResult) -> str:
    key = f"setup.reason.{check.id}"
    detail = setup_detail(check.detail)
    if check.ok:
        return detail or core_message(check.title)
    return _(key).format(detail=detail) if known(key) else detail or core_message(check.title)
