# APR-6 — OpenCode v2 static policies

База: 943a16a5. Проверенный CLI: **opencode v2.1.2** (version.txt).

Launcher передаёт permissions[] через OPENCODE_CONFIG_CONTENT; deny дополнительно
устанавливает hard policy tool.use=deny. MCP использует актуальный mcp.servers.
Выбор глобальной/проектной политики происходит перед запуском сервера. Для
автоматических режимов неподтверждённая версия или отсутствующий ingest блокируют
запуск, а не переводят его в обычный CLI. Capability и пояснение появляются только
после успешной проверки v2.1.2. Изменения применяются с новой сессии.

allow+dangerous=allow разрешает инструменты статически. allow+dangerous=ask —
**частичное покрытие**: автоматически разрешены только точные pwd и git
status/diff/log/branch без аргументов; остальные инструменты спрашивают нативно.
Статическая схема не заменяет классификатор AConsole. ask/scenario используют
нативные запросы. Inline-панель OpenCode не включена: блокировка модели до ручного
ответа через TUI/SSE не проверена.

Реальный model/tool path: tool_probe.py, tools/*.stdout/stderr/json. Настоящий
OpenCode работает с временными HOME/XDG и локальным Messages API; нет внешней модели
и пользовательских credentials. В allow+allow shell touch marker завершился и файл
создан. В deny инструменты отсутствуют в каталоге модели и принудительный shell
отклонён. В ask и allow+ask touch marker отклонён неинтерактивным CLI как требующий
подтверждения. Отдельный allow-ask-safe выполняет настоящий pwd (state=completed,
exit=0) без запроса. В этих прогонах AConsole daemon отсутствует: deny статический.

Отдельная проверка permission.create API (live/*.json) дала отличающиеся решения
для вызова без источника инструмента, включая allow в deny. Эти данные сохранены,
но **не приняты за доказательство выполнения/блокировки инструмента**. Проверка
401 без серверного пароля успешна. openapi.json.body сохраняет схему реального v2.

88 tests PASS, complete.xml; команда:
`python -m pytest tests/core/test_approval_runtime_policy.py tests/core/test_opencode_cursor.py tests/core/test_wire.py tests/ui/test_approval_modes.py -x -q --junitxml=artifacts/apr6/complete.xml`.
Ruff и strict basedpyright изменённых production-модулей PASS. Первоначальная
команда тестов включала несуществующий test_opencode_sessions.py и не запустила
проверки; исправлена только команда. Диагностический inspect.py был переименован
в inspect_cli.py после обнаружения перекрытия стандартного Python inspect.

UI light/dark/700px сняты с явными capability-фикстурами и просмотрены: пояснения
читаемы, горизонтальной обрезки и светлых пятен в тёмной теме нет. Вертикальная
прокрутка штатная; в снимке виден край нижней кнопки при выбранной позиции.

Настройки остаются доступны shell при разрешённом исполнении: TD-SEC7 не погашен;
режимы не являются файловой изоляцией или независимой security-приёмкой.
Установленное приложение не обновлялось.

Первичные источники для сверки схемы:
https://opencode.ai/docs/permissions/, https://opencode.ai/docs/config/,
https://opencode.ai/config.json. Живой tool path имеет приоритет над предположениями
по старой V1 API документации.
