# SEC-6: перепроверка существующей IPC-защиты

2026-11-06. Реализация c13283f в main, AConsole-Node: SEC-6.
Код IPC при этой перепроверке не менялся. Текущий main 6edcf49.

## Критерии и подтверждения

- hostrun test core tests/core/test_ipc_a.py: 14 PASS, exit 0.
- Полный hostrun test core на текущем main: 1779 PASS (artifacts/sec5/core.txt).
- same_mnt_namespace проверяет kernel peer PID, а не заявленный initialize.pid.
  Недоступный PID/namespace даёт отказ.
- test_outside_the_core_namespace_cannot_take_a_host_role: UI/CLI получают
  ROLE_FORBIDDEN, роль не установлена, предупреждение записано в caplog.
  Последующие settings/update, approval/decide, events/subscribe не допускаются.
- test_sidecar_outside_the_namespace_keeps_its_token_and_cannot_decide:
  валидный sidecar token допускает sidecar, а все три привилегированных метода
  возвращают ROLE_FORBIDDEN. Это эквивалентная подмена namespace/token port,
  разрешённая критерием узла; пользовательские токены не извлекались.
- Настоящий host UI-role клиент успешно работал с установленным ядром при
  активации и финальной сверке (artifacts/activation-b4183fc/verification.json).

## Дополнительная проверка работающего приложения

Из контейнера отправлены только initialize UI/CLI. Оба соединения закрыты до
ответа; настройки, решения и подписки не вызывались. Первая версия диагноста
ожидала JSON ROLE_FORBIDDEN и получила EOF; итоговый диагност записывает именно
ранний отказ, не выдавая его за JSON-ответ. Namespace/ROLE_FORBIDDEN и его лог
подтверждены отдельными эквивалентными тестами выше.

Установленные ipc/server.py и ipc/session.py байт-в-байт совпадают с main;
AST bootstrap.Daemon._client также совпадает. Хеши в native-probe.json.
Native-проверка подтверждает отказ доступа из текущего контейнера. Она не
подменяет отдельный тест одноразового sidecar token и не заявляет успешный
запуск нового агента. Действующие агенты и приложение не перезапускались.

Вывод: критерии SEC-6 подтверждены на существующей реализации и штатных
тестах, включая разрешённую критерием эквивалентную подмену namespace.
Изменений безопасности хостовых монтирований SEC-7 эта проверка не заявляет.
