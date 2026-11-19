# MEM-1 — бюджет RSS ядра AConsole 0.1.4

RSS ядра — **173.12 МиБ** (177280 КиБ). База TD-HDG-MEM-1 — 175.81 МиБ. Допуск — 8 МиБ, предел — 183.81 МиБ. Результат — **PASS**.

```sh
.venv-core/bin/python tools/ui_rss.py --pid 123502 --core-budget-report artifacts/0.1.4/rss.md
```

Измерено 2026-11-19 на исходниках с FND-2 (`aaf068f9`).
Кандидат запущен отдельно от пользовательского ядра через
`tools/hdag_acceptance.py serve --root <копия-стенда>` с `PYTHONPATH=src`.
SQLite-база и репозиторий взяты из стенда TD-HDG-MEM-1 (ревизия 2).
Стенд восстановил пять вкладок и пять вершин. Перед чтением `VmRSS`
выполнены MCP `plan_get`, `ctx_get` и IPC `app/snapshot`.
Первый замер после восстановления — 168.58 МиБ, повтор — 173.125 МиБ.
Это замер ядра для MEM-1. Квитанции Tier 3 требуют отдельной проверки.

[Исходное измерение TD-HDG-MEM-1](../hdag-0.1.3/memory-followup/tier3-current.json)
