# Исправление препятствий VLT-1 — 2026-11-18

Реализация долговечного эха — e7870fa9. Все 17 диагностик строгой типизации
изменённых тестов устранены. Добавлены аннотация часов, присваивания
неиспользуемых результатов и явная проверка `NoteRef is not None`.
Обращения тестов к закрытым фикстурам и точкам наблюдения отмечены
построчными `reportPrivateUsage` с объяснением. Этот же приём используется
в существующем test_vault_canvas.py. Проверка типов приложения, assertions
и frozen gate profiles не ослаблялись.

Проверки:

```sh
.venv-core/bin/python -m basedpyright tests/core/test_vault_writer.py tests/core/test_vault_wire.py
.venv-core/bin/python -m basedpyright
.venv-core/bin/python -m ruff check tests/core/test_vault_writer.py tests/core/test_vault_wire.py
.venv-core/bin/python -m ruff format --check tests/core/test_vault_writer.py tests/core/test_vault_wire.py
PYTHONPATH=src PYTHON_GIL=0 .venv-core/bin/python -m pytest \
  tests/core/test_vault_writer.py tests/core/test_vault_wire.py \
  tests/core/test_vault_canvas.py tests/core/domain/test_vault_sync.py \
  tests/core/test_writer_consistency.py tests/core/test_core_recovery.py \
  tests/core/test_persistence_b.py -o addopts='' -q -ra
git diff --check
```

Типизация тестов и полный production basedpyright: 0 errors, 0 warnings,
0 notes. Ruff и форматирование зелёные. Тесты: **68 passed in 50.03s**,
без skip/xfail. Исполнение — в контейнере агента с установленными зависимостями.

## Оставшиеся действия в работающем приложении

Micro/Blast повторно не запущены: VLT-1 остаётся blocked после прежней
эскалации f442689f130240aca24376085a. Execute-MCP отклоняет как node_update
в pending, так и node_claim. Согласно core/domain/plan.py, переход
blocked → pending требует human-роли. Попытка штатного CLI из контейнера
не прошла handshake; infrastructure/ipc/session.py разрешает CLI только
из mount namespace ядра. Ограничения и БД не менялись. Узел не объявлен done.

Для выполнения в обычном терминале хоста подготовлен apply_repair.py:

```sh
PYTHONPATH=src .venv-core/bin/python artifacts/vlt1-fix-2026-11-18/apply_repair.py resume-vlt1
```

После этого execute-вкладка сможет взять VLT-1 и продолжить pipeline.

repair-plan.json — подготовленный, **не применённый** запрос plan/save:
tools/rsgraph/** для IDX-3/IDX-0, protocol-пути подготовки Rust, отсутствие
test_server.py в IDX-0, tools/csgraph/** для IDX-4/IDX-0 и подключение
infrastructure/pipeline.py для LOD-1/LOD-0. В запросе есть ожидаемые хэши
контрактов из свежего MCP-снимка: устаревший запрос должен быть отклонён.
Это не снятие frozen и не замена узлов графа.

Поддержка repair_paths/repair_contracts/repair_note найдена в подготовленном
коде соседнего worktree c1b8f2ac852efabd. В эту ветку чужие изменения
не копировались; наличие механизма в установленном ядре этим отчётом
не подтверждается. После его штатной установки запрос можно подать:

```sh
PYTHONPATH=src .venv-core/bin/python artifacts/vlt1-fix-2026-11-18/apply_repair.py repair-scopes
```

Без аргумента helper только печатает JSON и не обращается к IPC.
Preview, строгая типизация helper и его lint проверены. Реальный ремонт
и разблокирование этим отчётом не выдаются за выполненные.
