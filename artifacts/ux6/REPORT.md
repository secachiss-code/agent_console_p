# UX-6 — README, действующие настройки и черновик

База: `944376a7`; plan-1/node-44. Изменения quota UI, уведомлений, app.py,
bootstrap и validation settings необходимы для найденных расхождений и отмечены
в note узла; существующие RPC-поля не менялись.

## Сверка README

| Раздел / обещание | Проверенная реализация и решение |
|---|---|
| Проекты, три режима, WorkSession, handoff, isolated | TabService, work_session_history, work_session_git и GTK new_tab/session picker. Обещания сохранены; параметры UI реализованы UX-5. |
| Resume, fork, каталог бесед | HAR-3 и test_native_sessions_wire; удалены два старых утверждения о fake-only session/list и продолжении родителя. Ограничения Cursor и OpenCode server сохранены. |
| Палитра | PaletteService + test_palette_wire и GUI; удалено утверждение о fake-only palette/*. |
| Клавиатура | MainWindow, AppViewModel и ShortcutManager применяют overrides. Обещание редактора в окне заменено точным описанием RPC keymap_overrides. |
| Терминал и sidecar | VTE/PTY, поиск, clipboard, signals реализованы. Удалено обещание включённого screen-watch для OpenCode; Cursor и event stream OpenCode разделены, HAR-4 остаётся открытым. |
| Согласования | ApprovalHold, ApprovalAlerts, panel; пояснение DENY есть после UX-5. Уточнено, что панель пока видна и поверх Git/review. Ограничения Write/Edit/MCP и pending APR-4…9 не скрыты. |
| Закрытие | UX-2 и native close_confirmation; удалено утверждение «закрывается сразу». Добавлена защита несохранённых настроек и при выходе приложения. |
| MCP и планы | PlanService, MCP registry и wire tests: 12 инструментов. Протокол lease/fence и DAG сохранены. |
| Vault | VaultSync, vault import/merge и persistence tests. Описана синхронизация файлов; запуск Obsidian не обещается. |
| Чекпоинты и Git | ReviewService, CheckpointService, session history, Git services и соответствующие tests/core. Уточнена область checkpoints_enabled/keep: safety и история WorkSession не отключаются этими настройками. |
| Лимиты | Readers Claude/Codex/Cursor/OpenCode, quota UI и tests. Пороги теперь действуют, применяются к used, даже когда показан remaining. Непубличные API остаются явно ограничением. |
| Настройки | Ниже перечислены реальные потребители каждого RPC-поля; GUI отдельно от дополнительных полей RPC. Убраны обещания отсутствующих редакторов. |
| Setup/health | Wizard/checks, diagnostics/health, preview/token, GUI tests. Автосоздание контейнеров и установка CLI не обещаются. |
| Архитектура, запуск, требования, выпуск | Соответствуют структуре contracts/core/UI/sidecar, install.sh и packaging. Исторические отчёты плана больше не выдаются за текущее состояние; 0.1.2 не объявлена выпущенной. |

Сверка основана на checkout и тестах, не является новой проверкой опубликованного
пакета, сторонних API или установленных CLI. Невыпущенные изменения описаны как
состояние исходников. Отдельные HAR/APR/SEC/QA-приёмки остаются в живом плане.

## Действие настроек

| Поля | Потребитель / момент применения |
|---|---|
| approvals_mode, approval_hold_seconds | ApprovalHold и маршрутизация native/inline, GUI настройки. |
| terminal_font, terminal_font_size, scrollback_lines, theme | MainWindow / TerminalPage, без перезапуска; прежние GUI-тесты двух retained terminals. |
| quota_warn_pct, quota_critical_pct, quota_display | Новые поля GUI, sidebar, header/banner, открытые details. Bootstrap пересчитывает существующие accounts и будущие observations; порядок warn ≤ critical валидируется до записи. |
| notify_when | ApprovalAlerts теперь применяет unfocused/always/never; never отзывает видимые уведомления, однажды прозвучавший эпизод не дублируется. Редактора поля в GUI нет. |
| vault_path | Bootstrap._use_vault / VaultSync переключает root. Дополнительное поле RPC. |
| worktrees_dir | HostTabs.set_worktrees_dir при старте ядра. README явно требует перезапуск и не обещает перемещение существующих копий. |
| checkpoints_enabled | ReviewService.on_user_prompt; safety и lifecycle snapshots остаются. |
| checkpoints_keep | Persistence cleanup / legacy tab checkpoints; не retention WorkSession history. |
| keymap_overrides | MainWindow → AppViewModel.apply_overrides / ShortcutManager; редактора GUI нет. |
| codegraph_languages | CodeGraphIndex читает Settings, отключает выбранный экстрактор и инвалидирует cache при изменении набора; редактирование через RPC. |
| setup_completed | SetupWizard / setup state; не пользовательский переключатель окна настроек. |

Дополнительная сверка выявила, что codegraph_languages лишь сохранялся. Теперь
он управляет текущими индексаторами Python/Rust/TypeScript, включая уже закэшированный
граф. Это не добавляет автоматический C#-индексатор: csharp остаётся совместимым
зарезервированным именем, README не обещает его поддержку.

## Черновик

Начальная загрузка делает поля нечувствительными, пока не получен baseline.
Внешние revisions обновляют только нетронутые поля. При закрытии изменённой
формы доступны Apply / Discard / Keep editing. Ошибка сохраняет форму, а успешный
ответ на более ранний patch не стирает более новые правки и не закрывает окно.
Выход приложения также проходит через protect_close. Discard закрывает родителя
через idle после response дочернего диалога.

## Проверки

- Целевые quota settings / wire / writer consistency / quota domain / contracts:
  32 passed, 15.39 с. Public RPC тест меняет пороги у существующей квоты,
  перезапускает временный Daemon, проверяет новые наблюдения и возврат к OK.
- Первый полный core/contracts: 1450 passed, 4 failed, 247.73 с, 12 warnings.
  Recovery fixture сообщала hook session `s` без transcript: добавлен временный
  transcript, проверки токенов не менялись. _gone в тесте OpenCode теперь считает
  ESRCH исчезновением процесса наравне с ENOENT. Два других отказа — palette max
  151.505 мс при лимите100 мс и writer max6.478 мс при лимите5 мс. Лимиты не менялись.
- Повтор recovery/opencode/palette/writer/quota: 70 passed, 21.76 с.
- Финальный полный core/contracts: **1454 passed**, 257.93 с, 12 прежних
  forkpty warnings; JUnit core-final.xml. Первый неуспешный прогон сохранён
  отдельно в core-results.xml. После старта финального прогона подключён
  codegraph_languages: его delta отдельно проверена тестами code_index_cache,
  code_impact, codegraph_protocol, quota_settings, writer_consistency и palette_wire:
  **30 passed**, 18.07 с. Новый cache regression не входит в число1454.
- Финальный native GTK/UI: **77 passed**, 9.26 с, одно прежнее предупреждение
  asyncio policy. Набор в xvfb-runner.txt. Три новых GTK-регрессии отдельно:
  3 passed, 3.22 с. Наборы пересекаются, результаты не суммируются.
- Strict basedpyright11 изменённых модулей: **0 errors / 0 warnings**.
  Ruff изменённых Python-файлов и git diff --check: **PASS**.

## Surprises & Discoveries / снимки

Использован gtk-screenshot-review; просмотрены9 PNG: preferences, unsaved,
quota-custom (нижняя часть формы), каждый light1280×800, dark1280×800 и light700×800.
Для снимков нативный UI venv и приватный Xvfb: hostrun относится к основному
checkout, GTK в devtools отсутствует. Конфигурация/состояние/Xauthority временные;
Xvfb извлечён из deb без установки, рабочий дисплей пользователя не используется.

- После добавления порогов низ формы прокручивается; отдельный снимок с фокусом
  Apply подтверждает доступность порогов, согласований и кнопки при700px.
- Переводы показываются текстом, modal-кнопки читаемы, тёмная тема без светлых
  участков. Верхняя и нижняя части формы проверены отдельно.
- Первый native тест неверно моделировал выбор response прямым emit до закрытия;
  тест теперь нажимает настоящую Gtk.Button после mapping. Временная попытка
  подписаться на closed внутри response была удалена: response уже приходит при
  закрытии, а его default handler выполняется после пользовательского handler.
  [Документация Adw.AlertDialog.response](https://gnome.pages.gitlab.gnome.org/libadwaita/doc/main/signal.AlertDialog.response.html).
  Реальный Discard потребовал idle, иначе родитель оставался открытым.
- Первой quota RPC-фикстуре не хватало connected/login metadata; включено только
  в тестовом источнике agents, production-фильтр авторизации не ослаблялся.
- docs/design/DESIGN.md отсутствует; совпадение с отсутствующим макетом не заявлено.

Настоящие пользовательские настройки, сервис и контейнеры не менялись. Проверки
не запускают оплачиваемые беседы. Успешный повтор performance-теста не закрывает
PERF-2; исходные превышения остаются в отчёте.
