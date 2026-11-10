# MCP-1: явный отказ несохранённого handover

База: `68268a43` (продукт идентичен `e1c3807a`).

`KnowledgeDatabase.save_handover()` при отсутствии строки проекта записывал
предупреждение и возвращал успех. `PlanBoard` после этого добавлял пакет в память
и подтверждал MCP-запрос. В SQLite пакета не было; перезапуск терял его.

Регрессионный сценарий через `mcp.Client(server)` с реальными SQLite, миграциями,
Writer и репозиториями подтвердил дефект: **1 FAIL / 2.27 s**, MCP `is_error=False`
при предупреждении «has no project row; it is not saved» (`before-regression.xml`).

Исправление повторяет существующую семантику записи фактов: отсутствие проекта
вызывает `ValueError`, граница репозитория преобразует его в `StoreWriteError`,
MCP возвращает `INTERNAL_ERROR`. Пакет не попадает в память и не подтверждается.
Новый сценарий затем создаёт проект/вкладку и повторяет запись; ответ и данные
после открытия нового engine подтверждают успешное сохранение.

После исправления связанные проверки: **32 PASS / 10.92 s** (`focused.xml`).
Это новый regression test, 20 сценариев QA-13 и существующие MCP/knowledge-тесты.
Прежний handover при отказе записи отдельно проверяется в QA-13. ResourceWarning
существующих тестов о незакрытых SQLite connections остаются.

Ruff и `git diff --check` PASS. Строгая проверка изменённого продуктового модуля
basedpyright: **0 errors, 0 warnings, 0 notes**. Для неё использован временный
JSON-конфиг с `extends` на `pyproject.toml`, `venvPath` на существующий
`/home/sas/Projects/sp_aconsole_prototype`, `venv=.venv-ui` и `include` на
изменённый модуль. Прямой запуск с отсутствующей локальной `.venv-ui` сначала
выдал ошибки импортов; это не результаты проверки настроенного окружения.

Полный core/contracts/gate/hostrun/smoke/packaging: **2127 PASS, 1 SKIP,
1 performance DESELECT, 27 warnings / 396.52 s** (`core.xml`).
GUI/performance/CI/deb install не повторялись.

## Воспроизведение

```sh
/path/to/.venv-core/bin/python artifacts/mcp1/run.txt --new-only
/path/to/.venv-core/bin/python artifacts/mcp1/run.txt
/path/to/.venv-core/bin/python artifacts/mcp1/run.txt --full
/path/to/.venv-core/bin/python artifacts/mcp1/summarize.txt
```

В этой среде тесты запускаются через `distrobox-host-exec`.
`--new-only` после исправления должен завершаться PASS; сохранённый before XML
относится к продукту до исправления.

Покрытие: **89.762274%**, 26659 строк + 5738 выходов ветвей из 28986 statements
+ 7106 branch exits. До 90% остаются **86 единиц**. Настоящий coverage gate:
**exit 2**, 89.76 < 90.00, QA-4 остаётся FAIL. Summarize объединяет полный core
с сохранённым полным GUI QA-9 только для неизменённых модулей: данные GUI для
изменённого `knowledge.py` удаляются перед объединением. Скрипт проверяет, что
с QA-9/QA-12 продукт изменился только в этом модуле. Это не новый полный GUI или
CI-прогон и не сравнение прироста на идентичном продукте. Порог 90% сохранён.
