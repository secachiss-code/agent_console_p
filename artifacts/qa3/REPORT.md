# QA-3 — проверки на настоящем ядре

Дата: 2026-11-09. База: `cc28325a`.

Рабочая ветка сначала обновлена fast-forward с `72612ff3` до локальной
`main` (`cc28325a`, 98 коммитов). Повторная реализация уже исправленных
UX-3/HAR-3 не потребовалась.

## Изменения

- `check_contracts.check_method_routes` запускает отдельное настоящее ядро,
  подключается по Unix IPC и проверяет все 83 записи `METHODS`.
  Невалидные параметры `[]` позволяют проверять маршруты без выполнения
  операций записи. `METHOD_NOT_FOUND`, `ROLE_FORBIDDEN`, внутренние ошибки,
  разрыв соединения и таймаут приводят к отказу проверки.
- Проверяющий процесс получает отдельные HOME, XDG-каталоги, SQLite и
  короткий путь сокета. `PYTHONPATH` явно выбирает `src` проверяемой ветки.
- Отрицательная проверка удаляет обработчик `palette/items`, сохраняя метод
  в таблице клиента, и подтверждает обнаружение `METHOD_NOT_FOUND`.
- Новый сквозной сценарий использует настоящий `CoreClient` и `TypedFacade`:
  добавление проекта, создание вкладки, получение launch spec, HTTP hook,
  запрос в подписанном UI store, решение через `ApprovalsViewModel` и ответ
  ожидающему hook. Отдельно проверяются ALLOW и DENY.
- Токен созданной вкладки используется для HTTP MCP initialize и tools/call.
  Создание плана и захват узла проверяются через IPC чтение и событие в
  UI store. После перезапуска ядра план и действующая аренда реальной
  сохранённой вкладки восстанавливаются из SQLite.

## Проверки

- Новые проверки: **5 PASS**, 9.36 с.
- Связанный набор: **77 PASS**, 0 FAIL / ERROR / SKIP, 33.931 с.
- Ruff для трёх изменённых Python-файлов: PASS.
- Ruff format для двух новых тестовых файлов: PASS.
- `git diff --check`: PASS.

Связанный набор запускался с `PYTHON_GIL=0` и включал:

```text
tests/core/test_api_routes.py
tests/core/test_application_wire.py
tests/core/test_wire.py
tests/core/test_plan_wire.py
tests/core/test_palette_wire.py
tests/core/test_native_sessions_wire.py
tests/core/test_approval_window.py
tests/core/test_mcp_tools.py
tests/core/test_mcp_auth.py
tests/core/test_mcp_managed_conformance.py
tests/contracts
tests/test_build_guards.py
```

Проверка подключена к `CHECKS` в `tools/check_contracts.py`; существующий
CI вызывает её через `tools/check.sh`, а новые сценарии — через `tests/core`.
Удалённый CI в рамках QA-3 не запускался.

## Границы результата

Ядро, IPC, HTTP, MCP, SQLite, клиент и UI store настоящие. В сценарии вкладки
внешний Podman заменён существующим тестовым исполняемым файлом; агентский
CLI не запускается. Проверка физических клавиш, окна GTK и матрицы реальных
CLI остаётся задачей AUD-2 / QA-6.

Sandbox запрещает bind Unix-сокета (`EPERM`); успешные прогоны выполнены
после разрешённого запуска вне sandbox с изолированными тестовыми данными.
