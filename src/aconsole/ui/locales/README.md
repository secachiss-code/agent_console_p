AConsole uses one gettext API in `aconsole.ui.i18n`. The old views/*/messages.py
modules only re-export it for existing import paths. The desktop locale selects
Russian or English; other locales fall back to English. No process-wide locale
changes are needed. User titles, paths, agent output and Git diagnostics are data
and are never translated or interpreted as format templates.

Edit the UTF-8 `en/LC_MESSAGES/aconsole.po` and `ru/LC_MESSAGES/aconsole.po`
catalogs, then run `python tools/compile_translations.py`. Commit PO and MO files
so wheels and deb source copies carry the same resources. The compiler uses only
the standard library. `tests/ui/test_i18n.py` checks reproducibility, placeholders,
plural boundaries, locale changes and loading from the deb source copy without
the checkout.

For a counter, use `_(key, count).format(count=count)`. Catalog plural entries
have msgid_plural `<key>.plural`. Russian has three forms, including the 11–14
exception. Use format_number/format_date for numeric and calendar presentation.
In Russian UI, use «рабочая копия» for worktree and «снимок» for checkpoint.
Keep Git commands, ref names and placeholders unchanged.

Core presentation codes are documented in contracts/messages.py. Existing toast,
notification and checkpoint scope string fields carry IDs. Setup detail can carry
a tagged setup_detail.v1 JSON value containing observation codes and separate
values; UI renders those values as data. Legacy prose and external diagnostic
text still display unchanged when no known code is present.
