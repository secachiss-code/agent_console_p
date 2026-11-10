# WS-1: восстановление изолированных вкладок

База: `9da08b2b49305b4dc04ff52da65bebd72571312d`.

QA-15 обнаружил реальную ошибку при перезапуске daemon с открытой изолированной
вкладкой. `_adopt` искал существующую WorkSession по пути вкладки. Путь worker
отличается от основного workspace, поэтому `add` с прежним ID перезаписывал
сессию: worktree, branch, title, status и времена заменялись значениями вкладки.
Очистка уже принятого результата после restart возвращала INVALID_PARAMS.

Новая регрессия через RPC, реальные Git, SQLite и миграции: active/archived ×
наличие/отсутствие основной вкладки. До исправления **4 FAIL / 8.55 s**,
точное сравнение WorkSessionView подтверждает подмену (`before-regression.xml`).

Теперь восстановление проверяет ID среди сессий проекта, включая архивные.
Существующая сессия сохраняется; создание отсутствующей старой сессии остаётся.
После restart проверки сравнивают все поля сессии, refs и связь worker, затем
успешно удаляют принятый worker worktree, сохраняя snapshot ref и основной результат.

Все связанные test_work_session*.py + test_work_sessions.py + новая регрессия:
**18 PASS / 28.45 s** (`focused.xml`). Ruff и git diff --check PASS.
Строгая проверка tabs.py через basedpyright с host .venv-ui:
**0 errors, 0 warnings, 0 notes**. GUI не менялся.

Воспроизведение в этой среде:

```sh
distrobox-host-exec /home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/ws1/run.txt --new-only
distrobox-host-exec /home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/ws1/run.txt
```

Полный core и пересчёт coverage выполняются в продолжающемся QA-15.
Порог QA-4 90% и остальные выпускные проверки остаются обязательными.
