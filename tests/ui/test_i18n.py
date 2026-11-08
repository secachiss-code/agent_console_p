"""Catalog integrity, locale switching and plural boundaries independent of installed locales."""

from __future__ import annotations

import gettext
import io
from datetime import UTC, datetime
from pathlib import Path
from string import Formatter

import pytest
from tools.compile_translations import compile_catalog

from aconsole.contracts.messages import UiMessage
from aconsole.ui import i18n
from aconsole.ui.viewmodels.logic.quota_fmt import format_money, format_tokens


@pytest.mark.parametrize("lang", ["en", "ru"])
def test_catalogs_are_reproducible_and_share_placeholders(lang: str) -> None:
    root = Path(i18n.__file__).parent / "locales"
    path = root / lang / "LC_MESSAGES/aconsole.po"
    compiled = compile_catalog(path.read_text())
    assert compiled == path.with_suffix(".mo").read_bytes()
    catalog = gettext.GNUTranslations(io.BytesIO(compiled))
    english = i18n._catalog("en")
    for key in english._catalog:
        if not isinstance(key, str) or not key:
            continue
        assert catalog.gettext(key) != key
        assert _fields(catalog.gettext(key)) == _fields(english.gettext(key))
    plurals = {key[0] for key in english._catalog if isinstance(key, tuple)}
    for key in plurals:
        for count in (0, 1, 2, 5, 11, 12, 14, 21, 22, 25, 101, 111):
            assert _fields(catalog.ngettext(key, key + ".plural", count)) == _fields(
                english.ngettext(key, key + ".plural", count),
            )


def _fields(text: str) -> set[str]:
    return {field for _, field, _, _ in Formatter().parse(text) if field is not None}


@pytest.mark.parametrize(("count", "word"), [(0, "дней"), (1, "день"), (2, "дня"), (5, "дней"),
    (11, "дней"), (14, "дней"), (21, "день"), (22, "дня"), (25, "дней"), (101, "день"), (111, "дней")])
def test_russian_plural_boundaries(monkeypatch: pytest.MonkeyPatch, count: int, word: str) -> None:
    monkeypatch.setattr(i18n.locale, "getlocale", lambda: ("ru_RU", "UTF-8"))
    assert i18n._("duration.days", count).format(count=count) == f"{count} {word}"
    assert i18n._("quota.panel.period", count).format(count=count) == f"Пул на {count} {word}"


def test_language_switch_formats_and_core_codes(monkeypatch: pytest.MonkeyPatch) -> None:
    moment = datetime(2026, 11, 8, 12, 34, tzinfo=UTC).astimezone()
    for name, title, money, tokens, date in (
        ("en_US", "Plan", "$1,234.50 / $2,000", "12.3K", moment.strftime("%Y-%m-%d %H:%M")),
        ("ru_RU", "План", "$1\N{NO-BREAK SPACE}234,50 / $2\N{NO-BREAK SPACE}000", "12,3K",
         moment.strftime("%d.%m.%Y %H:%M")),
        ("fr_FR", "Plan", "$1,234.50 / $2,000", "12.3K", moment.strftime("%Y-%m-%d %H:%M")),
    ):
        monkeypatch.setattr(i18n.locale, "getlocale", lambda name=name: (name, "UTF-8"))
        assert i18n._("plan.card.title") == title
        assert format_money(1234.5, 2000) == money
        assert format_tokens(12300) == tokens
        assert i18n.format_date(moment, year=True) == date
        for code in UiMessage:
            assert i18n.known(code)
            assert i18n.core_message(code) != code
        assert i18n.core_message("user text: {unformatted}") == "user text: {unformatted}"
    with pytest.raises(KeyError):
        i18n._("missing.catalog.key")


def test_deb_source_copy_can_translate_without_the_checkout(tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    from tests.test_packaging import _builder

    site = tmp_path / "site"
    _builder()._copy_app(site)
    for lang in ("en", "ru"):
        assert (site / f"aconsole/ui/locales/{lang}/LC_MESSAGES/aconsole.mo").is_file()
    code = (
        "import sys; sys.path.insert(0, sys.argv[1]); "
        "from aconsole.ui import i18n; "
        "assert i18n.__file__.startswith(sys.argv[1]); "
        "i18n.locale.getlocale=lambda:('ru_RU','UTF-8'); "
        "print(i18n._('duration.days',22).format(count=22))"
    )
    env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    result = subprocess.run([sys.executable, "-I", "-c", code, str(site)], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=20, check=True)
    assert result.stdout.strip() == "22 дня"


@pytest.mark.parametrize(("language", "mode"), [("en_US", "writable"), ("ru_RU", "доступна запись")])
def test_setup_observations_translate_and_preserve_paths(
    monkeypatch: pytest.MonkeyPatch, language: str, mode: str,
) -> None:
    import msgspec

    from aconsole.contracts.setup_detail import SetupDetail, SetupFact

    monkeypatch.setattr(i18n.locale, "getlocale", lambda: (language, "UTF-8"))
    path = "/user/path:with;{braces}"
    payload = msgspec.json.encode(SetupDetail(format="setup_detail.v1", facts=(
        SetupFact(code="setup.isolation.writable", values=("agent-example", path, "/mounted")),
    ))).decode()
    result = i18n.setup_detail(payload)
    assert path in result
    assert "agent-example" in result
    assert mode in result
    assert "setup.isolation" not in result
    assert i18n.setup_detail("raw: {diagnostic}") == "raw: {diagnostic}"
    assert i18n.setup_detail(None) == ""


@pytest.mark.parametrize(("language", "unknown"), [("en_US", "isolation is unknown"), ("ru_RU", "изоляция неизвестна")])
@pytest.mark.parametrize("code", ["setup.isolation.mount_unknown", "setup.isolation.mount_invalid",
                                  "setup.isolation.mount_unresolved"])
def test_setup_refusal_remains_explicit_in_both_languages(
    monkeypatch: pytest.MonkeyPatch, language: str, unknown: str, code: str,
) -> None:
    import msgspec

    from aconsole.contracts.app import CheckResult
    from aconsole.contracts.setup_detail import SetupDetail, SetupFact

    monkeypatch.setattr(i18n.locale, "getlocale", lambda: (language, "UTF-8"))
    payload = msgspec.json.encode(SetupDetail(format="setup_detail.v1", facts=(
        SetupFact(code=code, values=("agent-example",)),
    ))).decode()
    check = CheckResult(id="container_isolation", title="setup.check.container_isolation", ok=False,
                        severity="warning", detail=payload, fix_id=None, requires_confirmation=False)
    rendered = i18n.setup_check_reason(check)
    assert unknown in rendered
    assert "agent-example" in rendered
    assert "README" in rendered
    assert code not in rendered
