# SEC-12 — Git hostrun исполняется в devtools

База: `5a96265e`; plan-1/node-220. Найдено при QA-1.

## Подтверждённый обход

На временном репозитории в devtools построенный hostrun git diff имел argv
`git -C <repo> diff --no-ext-diff --no-textconv`. Настройка filter.evil.clean
и атрибут `f filter=evil` запустили маркерный shell-скрипт; diff вернул 0
и показал изменение. Такие флаги отключают external diff/textconv,
но не clean-фильтр. git status также вызывает core.fsmonitor.

Это отдельный путь от SEC-11: там общий GitRunner продукта, здесь hostrun.
Старый тест SEC-1 проверял external diff/textconv и не покрывал clean-filter.
Бывший host argv воспроизводится сценарием git-filter-probe.txt; запускать
его только внутри devtools. Пользовательский хост при воспроизведении
маркерный код не исполнял.

## Исправление

Все пять разрешённых Git-форм status, diff, log, worktree list, rev-parse HEAD
теперь используют podman start devtools и podman exec в проверенном cwd.
Allowlist и флаги diff сохраняются; аргументы не проходят shell.
Недоступный контейнер возвращает отказ, прямого host Git fallback нет.

Git для gate-review inspect также запускается только через Podman.
Каждое обращение проверяет запуск devtools; literal pathspec передаётся
в окружение самого контейнера. Ошибка start/exec прерывает просмотр.
Inspect остаётся диагностикой и не создаёт approval; доверенный PASS
и self-approval этим изменением не возвращаются. Политика описывает границу.

## Проверки

515 passed, 1 skipped (44.73 с): весь tests/hostrun и набор QA-1, перечисленный
в artifacts/qa1/REPORT.md. Единственный пропуск — списки CLI в отсутствующем
docs/plan; action allowlist и пример policy проверяются отдельно и проходят.
После уточнения типов тестового адаптера повторены test_server.py,
test_gate_reviews.py и test_session_cost.py: 168 passed, 24.38 с.

Новые HTTP-регрессии покрывают все пять Git-форм и отказ запуска devtools.
Отдельные тесты выполняют настоящие clean-filter и fsmonitor на временных
репозиториях: host argv допускает только podman, Git исполняется внутренним
тестовым адаптером внутри devtools. Это эмуляция внешнего Podman dispatch,
а не проверка независимости реальных mount namespaces.
Review-regression проверяет точные argv Podman, отсутствие host fallback
и прежние правила inspect/forged approvals.

Обычный Ruff исключает tools/hostrun и tests/hostrun. Принудительная полная
проверка сообщает 43 диагностики на неизменённых строках; изменённые строки
не имеют диагностик. Это не полный зелёный lint данных legacy-файлов.
git diff --check проходит.

## Граница результата

Маршрутизация проверена в текущих исходниках и исполняемых тестах.
Сервис hostrun пользователя не перезапускался; загруженный им старый код
не обновляется одним коммитом. Реальные mounts/HOME не менялись.
Изоляция контейнера от файлов хоста остаётся SEC-7; общая writable HOME
не становится безопасной только из-за podman exec. SEC-11 и независимая
приёмка GATE-1 этим узлом не закрываются.
