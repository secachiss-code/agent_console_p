# APR-8 — Cursor: неподтверждённые режимы недоступны

База: 8d97a5ed. Реальный CLI: **2026.11.13-eacade1** (version.stdout).

Проверены agent и agent -p с пользовательским ~/.cursor/hooks.json, preToolUse,
beforeShellExecution и failClosed:true. В полностью изолированном HOME оба запуска
завершаются Authentication required до hook/инструмента (results.json). Это **не**
подтверждение, что hooks не поддерживаются.

Затем выполнены четыре ограниченных прогона с существующей учётной записью CLI:
baseline --force, hook deny, failClosed с exit1, статический deny. Native CLI получал
временную непрозрачную копию auth; пользовательский файл не изменялся, credentials
не печатались и не сохранялись в артефактах. Каждый процесс ограничен40s, группа
останавливается, временный HOME очищается. Все четыре завершились timeout124 до
наблюдаемого инструмента, marker и hook events отсутствуют (live/results.json).
Успешного baseline нет, поэтому отсутствие marker **не принято за работающий deny**.

Дополнительная граница static-пробы: XDG_CONFIG_HOME влияет на расположение
cli-config.json. В исследованном пакете также есть CURSOR_CONFIG_DIR. Статическая
проба не доказывает загрузку написанного ~/.cursor/cli-config.json. Подменять этим
наблюдением реальный тест инструмента нельзя. Рабочие пользовательские hooks,
permissions и CLI config не редактировались.

Справка реального CLI подтверждает наличие --force, --sandbox, --trust и --endpoint;
в установленном коде есть загрузка hooks и failClosed. Ни справка, ни чтение кода
не заменяют пройденную tool-level проверку.

## Результат по режимам

| Режим | Состояние в AConsole |
| --- | --- |
| ask | Частично: обычный native Cursor, без панели AConsole и без заявления о полном перехвате. |
| allow + dangerous=ask | Неприменимо до успешной приёмки; запуск с автоматическим режимом остановлен. |
| allow + dangerous=allow | Неприменимо до успешной приёмки; --force не включён по одной лишь справке. |
| deny | Неприменимо до успешной приёмки; запуск остановлен, вместо опасного native fallback. |
| scenario | Недоступный stub в UI; stored значение следует native ask. |

Это предусмотренный objective APR-8 запасной вариант при неподтверждённом перехвате,
а не реализация подтверждённых Cursor approval modes. Launcher и UI теперь честно
показывают эту границу; отсутствие HOME/ingest не превращает выбранный auto mode
в обычный agent. В режиме ask существующая MCP-интеграция сохранена.

93 focused tests PASS (tests.xml), Ruff и strict basedpyright изменённых production
модулей PASS. UI light/dark/700px просмотрены: вся итоговая матрица и кнопка применения
видимы, переносы работают, message keys и светлые пятна в dark отсутствуют. Первые
preview ui-before-scroll сохраняют ошибочную позицию прокрутки фикстуры; после
перерасчёта высоты исправлена только фикстура, production layout не менялся.

Следующий технический шаг для Cursor: получить завершённый baseline tool call на
этой версии, затем проверить user/project hooks, deny и failClosed на том же пути.
До этого auto modes не рекламируются как защита. Установленное приложение не обновлено.

Первичные источники:
https://cursor.com/docs/cli/reference/permissions,
https://cursor.com/docs/hooks,
https://cursor.com/docs/cli/reference/parameters.
