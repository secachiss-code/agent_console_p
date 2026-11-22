# PL-2: атомарная сборка графа

HEAD: `9f0fc40af3f33f4d92994673a0599cd3bf4d16b1` (правки узла коммитятся следом).

Окружение: Python 3.15 free-threading, pytest через `hostrun test core` в checkout `core` (`/home/sas/Projects/.aconsole-worktrees/sp_aconsole_prototype/core`). Новой миграции нет.

Команда контракта:

```sh
hostrun test core tests/core/test_plan_crud.py tests/core/test_plan_store.py tests/core/test_plan_materialize.py
```

Поведение: preview считает graph token от `PlanTopology.revision_no`, контрактов и рёбер. Commit только добавляет новые ключи и пишет receipt в той же транзакции. Цикл, живой lease, заморозка и смена существующего ключа отказывают до записи. Чужой token без receipt — `GRAPH_TOKEN_STALE` (4022) и ничего не пишет. Тот же token возвращает прежние id. Сбой записи откатывает память и не зовёт vault; повтор вставляет те же id. Старые id, статусы и `node_attempts` остаются.

Результат: попытка 3 (`f4a68ccb54b34d57ad221301b7`) прошла. Micro verify — 5 проверок, 0 отказов. Blast — `2 passed, 0 failed` (узел `node-1064`, статус `verifying`: `review_required` не даёт исполнителю поставить `done`). Локально до гейта те же три модуля — 13 passed.

Остаточный риск: код `4022` живёт в MCP `_CODES`, не в реестре `ERRORS` — файл реестра вне scope узла. Blast кэширует отказ, пока не сменится attempt или запись кэша.
