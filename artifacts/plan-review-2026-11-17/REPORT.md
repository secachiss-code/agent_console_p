# Сверка плана AConsole — 2026-11-17

Проверены исходники `b08766b0`, история после исходной базы плана
`e14f1c99d186`, `pyproject.toml`, приёмочные артефакты IDX-1 и текущая доска
`plan-900` через MCP. Снимок доски сохранён в `plan-900.json`.

Источник текущего плана — `plans/0.1.4-index.md`. Его заголовок и краткое
возвращение актуализированы: пакет 0.1.3+d, реализация идёт, план frozen.
Раздел 0 теперь различает реализацию и приёмку для всех 23 узлов, содержит
ссылки на коммиты и порядок продолжения. Раздел 1 помечен историческим
срезом; начальный список ready и оценки не выдаются за текущий остаток.

Приняты IDX-1 и VLT-3. Код IDX-2/3/5 уже присутствует, но доска показывает
pending/blocked/blocked. Их повторная реализация не нужна: впереди
проверка окружения и pipeline-приёмка. Остальные L1 требуют реализации;
ни одно направление L0 не закрыто. Требуется настоящий digest образа.
Статусы, frozen-контракты и приёмочные квитанции этим обзором не менялись.
Первоначальная причина blocked из краткого снимка не устанавливается.

Обнаружен регрессионный тест в `tests/hostrun/test_server.py`, ожидавший
старый TypeScript argv с `npx --yes`. Обновлён на запуск адаптера через
предустановленные инструменты: абсолютные пути, версия 18.7.0, PYTHONPATH
worktree, отдельный аргумент пути и отсутствие установки в горячем пути.
В том же файле устранены три прежних замечания Ruff: порядок импортов,
составной assert и длинная сигнатура. Код приложения не менялся.
В плане отмечено необходимое расширение scope IDX-0: его direction-команда
покрывает весь tests/hostrun, а paths не включает test_server.py.

Проверки:

```sh
.venv-core/bin/python -m pytest \
  tests/core/test_code_index_cache.py \
  tests/core/test_codegraph_protocol.py \
  tests/core/test_codegraph_py_ts.py \
  tests/core/test_codegraph_rust.py \
  tests/core/test_code_graph_store.py \
  tests/core/test_merge_migrations.py \
  tests/core/test_vault_canvas.py tests/hostrun -o addopts='' -q -ra
.venv-core/bin/ruff check tests/hostrun/test_server.py
.venv-core/bin/python -m pytest tests/hostrun/test_server.py -o addopts='' -q -ra
git diff --check
```

Общий прогон: **312 passed, 1 skipped**, 45.70 с (`tests.txt`).
Пропущен test_allowlist.py:50: списки клиентов находятся в связанном docs/,
которого нет в этом checkout. Реальные Rust/syn и TypeScript-фикстуры
прошли; это не проверка развёрнутого hostrun и не direction-квитанция.
Ruff и проверка пробелов прошли. После исправления форматирования повторён
весь test_server.py; результат — в `hostrun-server.txt`.

UI не менялся. Снимки UI и повторный ресурсный замер для этой правки не
требовались. Исторический долг памяти не закрывался.
