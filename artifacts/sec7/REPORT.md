# SEC-7 — диагностика и инструкция изоляции

База: `d2ea3aa`. Узел `plan-1/node-21` остаётся **in_progress**.

## Реализовано

- Мастер получает девятую проверку `container_isolation`: read-only
  `podman inspect --format '{{json .Mounts}}'` для агентов и devtools.
- Выявляются mounts HOME и его родителей, базы/SQLite sidecars, XDG config,
  shell startup, systemd, SSH/AWS, hostrun state/socket/host-bins и runtime.
  Учитываются альтернативные Destination, read-only binds и алиасы Source.
- Ошибка inspect/list, невалидный JSON/path, неразрешимый Source и превышение
  лимита метаданных дают неизвестную изоляцию, а не успех. Большие списки
  контейнеров не обрезаются незаметно. Контейнерные профили для этой проверки
  не исполняются, тестовые записи в пользовательские файлы не выполняются.
- Удалена рекомендация клонирования контейнера с `$HOME:$HOME` и создания
  стандартного Distrobox без выбора mounts; вместо неё даётся ссылка на
  инструкцию изоляции и безопасный `podman create --help`.
- Окно состояния показывает непроверенную изоляцию и отдельную кнопку запуска
  диагностики. Результат и инструкция выводятся с переносом; проверка не
  добавлена в двухсекундный health poll. Старое ядро без нового check id даёт
  неизвестный статус. Wire-контракты не менялись.
- README содержит минимальную конфигурацию с отдельным HOME и binds только
  worktrees/vault, шаблон rootless Podman, read-only проверки и ограничения.
  Опции сверены с [официальной документацией Podman](https://docs.podman.io/en/latest/markdown/podman-create.1.html).

Изменения потребовали adapter setup_checks и UI health: только изменение
application/services/setup.py не позволило бы получить mounts и показать их.

## Проверки

- Текущий worktree в devtools: `tests/core/test_setup_wizard.py` — **65 passed**,
  включая новый check через существующий setup RPC, unsafe/read-only/alias/XDG
  mounts, разрешённые worktree/vault binds, unknown и oversized metadata.
- На отдельном GTK Broadway: `tests/gui/front/test_health_dialog.py` —
  **8 passed**, одна прежняя deprecation warning gi.events. Проверены результаты
  warning/success/unavailable и восстановление активности кнопки.
- Ruff изменённых Python-файлов и `git diff --check` — успешно.

## Surprises & Discoveries — визуальная проверка

Применён навык gtk-screenshot-review. Штатный ui-snapshot не имеет health view
и привязан к основному checkout, поэтому использован отдельный GTK preview
текущей рабочей копии. Devtools не имеет xvfb-run и libpango, а host
isolated_x не нашёл Wayland socket. Установки пакетов не выполнялись.

Успешный обход для проверки UI — host GTK Broadway на localhost с отдельным
runtime и очисткой процесса после тестов, без соединения с пользовательским
дисплеем. Preview-runner сохранён в `ui-runner.txt` (запуск Python из UI venv).
Предупреждение в preview синтетическое, данные пользователя не читаются.

Broadway не отдаёт render node отдельной поверхности Adw.Dialog. Первые пустые
снимки не использованы; финальные снимки показывают настоящий GTK toolbar/page
диалога, временно помещённый в отдельное окно. Это проверка содержимого,
не обрамления modal dialog. Backend ограничил wide кадры размером 1024×768
вместо запрошенных 1280×800, narrow — 700×768.

Просмотрены `ui/health-light.png`, `ui/health-dark.png`, `ui/health-narrow.png`:
ключи переведены, warning и кнопка читаются, текст переносится, обрезания на
700 px нет, светлых участков в тёмной теме нет. `docs/design/DESIGN.md` в этом
checkout отсутствует; сравнение с макетом не заявляется.

## Что остаётся для приёмки SEC-7

Реальные контейнеры, mounts, HOME и установленные core/UI не менялись.
Монтирования пользователь ещё не подтвердил; в инструкции использован
предложенный минимальный набор. Нужно выбрать допустимые каталоги, вручную
создать изолированную конфигурацию, проверить её inspect и невозможность
доступа к настоящим файлам хоста. Тесты проверяют классификацию рекомендованных
binds, а не доказывают kernel isolation живого контейнера.

Неизолированные текущие контейнеры продолжают иметь прежние права. Проверка
mounts не является аудитом образа, privileged/host PID, IPC или ядра ОС.
Подтверждение реальной конфигурации и проверка modal UI на штатном display
остаются условиями закрытия узла. GATE-1 по-прежнему ждёт SEC-7.
