# PL-1: хранилище редакций концепций

HEAD: `a1c93b9eacef5cfe980382b4f1686e996f7c8610` (правки узла не закоммичены).

Окружение: Python 3.15.1 free-threading, pytest через `hostrun test core` в checkout `core`. Ревизия `0014_concept_revisions` — единственная голова Alembic поверх `0013_note_fts`.

Команда контракта:

```sh
hostrun test core tests/core/test_migration_worker.py tests/core/test_concept_store.py tests/core/test_plan_store.py
```

Поведение: `expected == 0` создаёт ревизию 1 и первый снимок. Следующая запись увеличивает ревизию только при совпадении `expected`. Чужой `expected` даёт `CONCEPT_REVISION_CONFLICT` (4020) и не меняет ни этот ключ, ни соседний. Ключ вне `^[A-Z]{1,4}-\d{1,4}$` — `CONCEPT_KEY_INVALID` (4021). Неизвестный проект — `PROJECT_NOT_FOUND` (2001).

Результат: попытка 4 прошла. Micro verify — 5 проверок, 0 отказов. Blast — `2 passed, 0 failed` (узел `node-1063`, статус `done`). Локально до гейта: `tests/core/test_concept_store.py`, `test_migration_worker.py`, `test_plan_store.py` — 13 passed.

Остаточный риск: попытка 3 один раз упала на `tests/core/test_persistence_b.py::test_persistence_b_tick` (max 0.011 с при пороге 0.005 с, p99 около 8 мкс) при посторонней нагрузке; повтор на том же дереве прошёл. Следующая миграция не может снова занять идентификатор `0014`.
