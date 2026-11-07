# AConsole — полное описание проекта (версия 0.1.1)

Документ описывает проект в состоянии **выпущенной версии 0.1.1 «Работа с Git»**
(тег `v0.1.1`, коммит `45152a1`, дата релиза 2026-11-06). Всё, что появилось в `main`
после этого тега, здесь не описано: документация намеренно соответствует установленной
версии, а не текущему дереву разработки.

| Источник | Значение |
|---|---|
| Тег | `v0.1.1` |
| Коммит тега | `45152a1` (`45152a13f58f2937a43a307f08ab488aca628602`) |
| Версия пакета, исходников и ответа ядра | `0.1.1` |
| Версия API контрактов | `1.4.0` (`aconsole.contracts.API_VERSION`) |
| Версия протокола IPC | `1.0` (`major=1`, `minor=0`) |
| Capability-маркер сессий | `work_sessions.v1` |
| Вершина миграций | `0006_session_history` |
| Опубликованный пакет | `aconsole_0.1.1_amd64.deb`, 67 586 408 байт, SHA-256 `23b001936f03cc2f15c9ffdcc6fbc0b68235d212b817eea8e8bf44a2ded2de16` |
| Лицензия | MIT, см. [LICENSE](LICENSE) |

Смежные документы: [README.md](README.md) — краткая выжимка, [ISSUES.md](ISSUES.md) —
журнал проблем версии, [CHANGELOG.md](CHANGELOG.md) — история версий,
[plans/0.1.1-git.md](plans/0.1.1-git.md) — план реализации,
[artifacts/git-0.1.1/VERIFICATION.md](artifacts/git-0.1.1/VERIFICATION.md) — приёмка.

---

## 1. Что это такое

AConsole — настольное приложение для Linux, которое ставит несколько CLI-агентов
(Claude Code, Codex CLI, OpenCode, Cursor CLI) рядом друг с другом и добавляет вокруг них
инфраструктуру, которой у самих CLI нет:

- рабочая сессия задачи владеет рабочей копией, веткой и историей, а не отдельная вкладка;
- согласования опасных инструментов с карточкой поверх терминала и системными уведомлениями;
- доска планов с MCP-инструментами, lease узлов и базой знаний в Obsidian-vault;
- чекпоинты рабочей копии на side-refs, восстановление файлов с preview и confirm-токеном;
- страница Git: состояние worktree, граф, diff, stage/commit, merge/rebase/cherry-pick,
  очистка worktree, отправка diff другому агенту;
- панель лимитов и расходов по подпискам четырёх агентов.

### 1.1. Статус

Это **рабочий прототип**, проверенный на Linux-машине автора. Публичного обещания
стабильного API нет: контракты версионируются (`1.4.0`), но могут меняться между релизами.
AConsole не песочница и не замена контейнерной изоляции — агент работает с правами
пользователя внутри контейнера и видит всё, что в него смонтировано.

### 1.2. Границы ответственности

AConsole **делает**: запускает CLI в существующем контейнере через `podman exec`,
управляет worktree и ветками, снимает чекпоинты, принимает hooks и события, ведёт планы
и знания, считает лимиты, показывает Git.

AConsole **не делает**: не создаёт контейнеры, не устанавливает и не логинит CLI агентов,
не настраивает монтирование, не подписывает пакет GPG, не изолирует файловую запись
параллельных процессов (для этого нужны отдельные worktree), не выполняет multiuser-
развёртывание.

### 1.3. Масштаб кодовой базы (тег `v0.1.1`)

| Метрика | Значение |
|---|---|
| Python-файлы в `src/` | 263 |
| Строк в `src/` | 45 745 |
| — `core` | 150 файлов |
| — `ui` | 86 файлов |
| — `contracts` | 15 файлов |
| — `sidecar` | 6 файлов |
| Тестовые файлы | 217 |
| Тестовые функции | 1 215 |
| RPC-методы | 81 |
| События UI | 22 |
| Коды ошибок | 77 |
| Таблицы SQLite | 27 |
| Миграции Alembic | 6 (`0001`…`0006`) |
| MCP-инструменты | 12 |
| Горячие клавиши по умолчанию | 35 действий |

---

## 2. Возможности версии 0.1.1

### 2.1. Проекты, рабочие сессии задач и worktree

- Проект — любой Git-репозиторий. Базовая ветка берётся из `git symbolic-ref --short HEAD`
  (fallback `main`), а не считается заранее равной `main`.
- Проект содержит несколько **рабочих сессий** (`WorkSession`). Сессия владеет основной
  рабочей копией, веткой (`branch`), базовой веткой (`base_branch`), историей и статусом
  `active` / `archived`. Идентификатор: 16 hex-символов (`os.urandom(8)`) у сессии из
  `workSession/create`; у сессии, созданной вместе с вкладкой, — `s-<tab_id>`.
- Нативный диалог CLI (`agent_session_id`) отделён от `work_session_id`: первый нужен
  для resume конкретного CLI, второй — для задачи.
- Три режима основной копии (`WorkspaceMode`):
  - `new_worktree` — новый worktree с веткой `ac/s-<slug>-<id>`;
  - `main_checkout` — работа прямо в корне проекта;
  - `existing_worktree` — уже существующий каталог.
- Каталог worktree по умолчанию — `$XDG_DATA_HOME/aconsole/worktrees/<repo>/<tab>`
  (`~/.local/share/aconsole/worktrees/...`); переопределяется настройкой `worktrees_dir`.
- Один физический основной workspace не может принадлежать двум активным сессиям:
  нарушение даёт `WORK_SESSION_WORKSPACE_CONFLICT`.
- **Эстафета.** В общем каталоге работает один агент. Передача управления
  (`workSession/handoff`) останавливает прежнего исполнителя, проверяет отсутствие
  процессов (orphan scan по маркеру `ACONSOLE_TAB`), делает снимок `handoff` и только
  затем разрешает запуск следующего. Простое переключение вкладки право записи не даёт.
  Если остановку нельзя подтвердить, запуск блокируется (`WORKTREE_BUSY`).
- **Изолированный исполнитель.** Получает detached worktree от зафиксированного HEAD
  сессии на ref `refs/aconsole/sessions/<sid>/workers/<tab>/start`. Результат
  (`workSession/result`) сохраняется как снимок `result`; `workSession/integrate` вливает
  его в ветку сессии через merge, проверяя, что `source_oid`/`target_oid` из запроса
  совпадают с текущими ref (иначе `MERGE_PREVIEW_STALE`). В 0.1.1 сам preview ещё
  строится от изменяемого ref, а не от зафиксированного OID (см. §12.5).
- **Закрытие вкладки** сохраняет рабочие копии и историю. **Архивирование сессии**
  (`workSession/close`) останавливает агентов сессии; сессию с сохранённой копией можно
  возобновить (`workSession/resume`). Удаление управляемой основной копии — отдельное
  проверяемое действие (`git/cleanupWorktree`, `workSession/cleanupWorker`).
- Перед очисткой проверяются новые коммиты, dirty-состояние и ignored-файлы;
  игнорируемые файлы удаляются только при `discard_ignored`.
- Для проекта видно ahead/behind, staged/unstaged/untracked/conflicted, состояние
  `merge`/`rebase`/`cherry-pick`/`revert`/`bisect` и preview слияния.

### 2.2. Агенты и контейнеры

| Агент | `AgentKind` | Команда | Контейнер |
|---|---|---|---|
| Claude Code | `claude` | `claude` | `agent-claude` |
| Codex CLI | `codex` | `codex` | `agent-codex` |
| OpenCode | `opencode` | `opencode` | `agent-opencode` |
| Cursor CLI | `cursor` | `agent` | `agent-cursor` |

- Контейнер ищется по имени `agent-<kind>`; остановленный контейнер считается доступным
  (ядро запускает его через `ensure_container`).
- Команда выполняется как `podman exec` с `--user <host user>`, login shell и окружением
  вкладки (`ACONSOLE_TAB`, `ACONSOLE_TAB_TOKEN`, `ACONSOLE_PORT`, PATH из Distrobox).
- В `AgentKind` есть значение `grok`, но адаптера, контейнера и набора возможностей у него
  в 0.1.1 нет: в список агентов (`_AGENTS`) он не входит.
- Логин определяется по наличию файла в домашнем каталоге контейнера:
  `.claude/.credentials.json`, `.codex/auth.json`, `.config/cursor/auth.json`;
  для OpenCode проверяется его база (см. `opencode.limits`). Для Claude дополнительно
  используется `claude auth status`.
- Статусы вкладки (`TabStatus`): `starting`, `idle`, `working`, `waiting_approval`,
  `waiting_input`, `error`, `exited`, `unknown`. Вкладка с признаком внимания
  подсвечивается и порождает уведомление.

Матрица возможностей (`core/domain/agent.py`, `CAPABILITIES`):

| Возможность | Claude | Codex | OpenCode | Cursor |
|---|---|---|---|---|
| `hooks` | да | после проверки CLI | — | — |
| `statusline` | да | — | — | — |
| `limits` | да | да | да | да |
| `resume` | да | да | да | да (в UI недоступно, см. §12.5) |
| `fork` | да | да | — | — |
| `skills` | да | — | да | да |
| `approvals` | да | после проверки CLI | — | — |
| `checkpoints` | да | после проверки CLI | — | — |
| `mcp_http` | да | да | да | да |

Команды продолжения диалога, которые строит ядро (`_launch_command`):

| Агент | Новая сессия | Resume | Fork |
|---|---|---|---|
| Claude | `claude` | `claude --resume <id>` | `claude --resume <id> --fork-session` |
| Codex | `codex` | `codex resume <id>` | `codex fork <id>` |
| OpenCode | `opencode` | `opencode -s <id>` | — |
| Cursor | `agent` | `agent --resume <id>` | — |

Идентификатор нативной сессии узнаётся из hooks (Claude), события `codex_session`
(Codex) и потока OpenCode; `reported_session` принимает его только от Claude и OpenCode.

### 2.3. Терминал и палитра

- VTE-терминал: прокрутка, изменение размера, поиск по экрану (`Ctrl+Shift+F`),
  масштабирование, копирование и вставка.
- Между VTE и агентом стоит **sidecar** на стандартной библиотеке Python: держит PTY,
  передаёт `SIGWINCH`/`SIGTERM`/`SIGHUP`, шлёт в ядро `started`/`stats`/`exited` и
  сигналы экрана, а для Cursor определяет «экран затих» (`ACONSOLE_SCREEN_WATCH=1`).
- Буфер обмена терминала: `Ctrl+Shift+C`/`Ctrl+Shift+V`, контекстное меню по правому клику,
  `Menu`/`Shift+F10` (копировать, вставить, вставить изображение, выделить всё);
  `Ctrl+C` копирует выделение и остаётся прерыванием без него.
- Вставка PNG идёт через ядро: файл с валидацией PNG (подпись, CRC чанков, 8 МиБ,
  32 мегапикселя) сохраняется приватно в `<worktree>/.aconsole/clipboard/`, путь
  добавляется в `.git/info/exclude`, проверяется чтение из контейнера, после чего в
  терминал вставляется путь без автоматического Enter. Лимит поля `png` в контракте
  `terminal/prepareImagePaste` — 8 МиБ.
- Командная палитра (`Ctrl+Shift+P`) ищет команды, навыки агента, узлы планов, заметки
  vault, вкладки, сессии, действия и согласования; имеет отдельный шаг аргумента и
  ранжирование с запоминанием (frecency). В 0.1.1 палитра работает только на fake core:
  в настоящем ядре методы `palette/*` не обслуживаются (см. §12.5).
- Горячие клавиши переопределяются в настройках. Действия по умолчанию:

| Действие | Клавиши |
|---|---|
| `palette` | `Ctrl+Shift+P` |
| `new-tab` | `Ctrl+Shift+T` |
| `close-tab` | `Ctrl+Shift+W` |
| `next-tab` | `Ctrl+Page_Down`, `Ctrl+Tab` |
| `previous-tab` | `Ctrl+Page_Up`, `Ctrl+Shift+Tab` |
| `tab-1`…`tab-9` | `Alt+1`…`Alt+9` |
| `next-attention` | `Ctrl+Shift+Y` |
| `approve` / `deny` | `Ctrl+Shift+Enter` / `Ctrl+Shift+Backspace` |
| `attention-next` / `attention-previous` | `j` / `k` (в карточке) |
| `attention-allow` / `attention-deny` | `a` / `d` (в карточке) |
| `attention-open` | `Return` (в карточке) |
| `diff` | `Ctrl+Shift+D` |
| `git` | `Ctrl+Shift+G` |
| `terminal-search` | `Ctrl+Shift+F` |
| `copy` / `paste` | `Ctrl+Shift+C` / `Ctrl+Shift+V` |
| `toggle-sidebar` | `F9`, `Ctrl+Shift+B` |
| `settings` | `Ctrl+,` |
| `shortcuts` | `Ctrl+?`, `F1` |
| `zoom-in` / `zoom-out` / `zoom-reset` | `Ctrl+=` / `Ctrl+Alt+-` / `Ctrl+0` |
| `quit` | `Ctrl+Q` |
| `send-next-key` | `Ctrl+Shift+Alt+K` |

### 2.4. Согласования

- Источник — hooks Claude (`PermissionRequest`) и Codex. Для Codex ядро дважды
  обращается к `codex app-server`: получает `hooks/list`, сверяет семь собственных
  command-hooks и добавляет их индивидуальные хэши в `hooks.state`, затем проверяет
  статус `trusted`. Постоянный `~/.codex/config.toml` не изменяется; чужие hooks доверия
  не получают.
- Пока проверка не прошла, Codex не заявляет `hooks`, `approvals`, `checkpoints`:
  согласования остаются в терминале, автоматические чекпоинты выключены, приходит
  уведомление с инструкцией открыть `/hooks`.
- Карточка запроса показывается поверх терминала активной вкладки: инструмент, команда,
  рабочий каталог, риск, срок действия. Кнопки всегда видимы, прокручиваются только детали.
  Карточка не перехватывает клавиатуру: переход — `next-attention`.
- Если окно неактивно, приходит системное уведомление (`Gio.Notification`, HIGH);
  одно на эпизод, отзывается при решении или возврате в окно.
- Решение: разовое (`once`) или на сессию вкладки (`tab_session`); отказ может нести
  сообщение агенту; запрос можно вернуть в нативный prompt (`approval/releaseToNative`).
- У запроса есть `nonce`, `expires_at` и дедупликация по входным данным: повторная
  доставка того же события не создаёт вторую карточку, чужой nonce отвергается.
  Разовый `ALLOW` не покрывает независимый повторный вызов с теми же session/tool/input.
- Риск-теги (`RiskTag`): `destructive`, `outside_worktree`, `network`, `publish`,
  `secrets`, `privilege`. Неразобранная команда считается рискованной.
- Запомненное правило по префиксу работает только для пар «программа + подкоманда» из
  встроенного allowlist (`ALLOWLIST_VERSION = 2`): `git status`, `git log`, `git diff`,
  `git show`, `git branch`, `ls`, `cat`, `rg`; только для «простых» слов без раскрытия
  shell и только пока пути остаются внутри worktree. `pytest` в allowlist не входит,
  потому что импортирует код из репозитория. Для `git log -p/--stat`, `git diff`,
  `git show` обязательны `--no-ext-diff` и `--no-textconv`.
- Детали запроса: `CommandDetail` (команда, preview аргументов), `EditDetail` (файлы,
  preview diff), `GenericDetail` (JSON-preview). Полнота этих preview ограничена (§12.5).
- OpenCode и Cursor hooks не имеют: их запросы остаются нативными запросами терминала.

Согласования — дополнительный барьер, а не замена изоляции контейнера.

### 2.5. Планы, MCP и база знаний

Ядро поднимает MCP-сервер (streamable HTTP) на `/mcp` того же loopback-приложения, что и
ingest. Каждая вкладка получает bearer-токен, поэтому инструменты работают в контексте
её проекта, worktree и агента.

| Инструмент | Режим | Назначение |
|---|---|---|
| `plan_create` | idempotent | создать план; повтор с тем же названием возвращает тот же план |
| `plan_add_nodes` | idempotent | добавить узлы; существующие ключи не меняются, цикл не добавляется |
| `plan_get` | read | прочитать план; `response_format=detailed`, `since=<revision>` |
| `plan_ready` | read | узлы, доступные к взятию, по приоритету (`limit` 1…20) |
| `node_claim` | write | взять lease узла и получить fence |
| `node_update` | write | статус, heartbeat, заметка (≤2000), evidence (≤4000) |
| `node_attach` | idempotent | артефакт `commit`/`file`/`test_report`/`url`/`note` |
| `ctx_get` | read | знания по узлу или запросу (`max_items` 1…8) |
| `kb_add` | write | факт, решение или требование (тело ≤8000); источник факта обязателен |
| `kb_impact` | read | окрестность цели по графу кода (`hops` 1…3, `limit` 1…30) |
| `handover` | write | записать или прочитать handover текущей работы |
| `plan_list` | read | планы проекта (`active`/`all`) |

План — DAG: ключи, зависимости, родитель, связанные узлы, владелец, пути, критерии
приёмки, тестовая команда, флаг review, границы работы. Статусы узла: `pending`,
`in_progress`, `verifying`, `blocked`, `done`, `failed`, `cancelled`. Узлы берутся через
lease с fence (`LEASE_HELD`, `LEASE_STALE_FENCE`, `LEASE_EXPIRED`), поэтому два агента не
должны незаметно работать над одним узлом. Ответы инструментов сжаты: без отступов,
пустых полей и лишних схем; ядро считает байты ответов MCP, попавшие в контекст агента.

Синхронизация с Obsidian-vault:

- по умолчанию `~/Obsidian/AConsole` (иначе `Settings.vault_path`);
- узлы плана — `plans/<plan>/<KEY>.md` с YAML frontmatter;
- тело заметки под frontmatter после первой записи принадлежит пользователю;
- редактирование frontmatter проходит трёхсторонний merge: принятые поля возвращаются
  на доску, отклонённые записываются обратно (`VAULT_CONFLICT`, `VAULT_EDIT_REJECTED`);
- заметки `facts/<project>-to-review.md` перечисляют факты, код вокруг которых изменился;
- `.obsidian/` не источник истины; ссылки за пределы vault не разрешаются, симлинки
  наружу не разыменовываются.

Граф кода (`codegraph`): языки `python`, `ts`, `rust-metadata`, `csharp`; Python
сканируется локально, остальные — через экстракторы в контейнере `devtools`. Один граф на
корень проекта, кэш с TTL, ошибка экстрактора — `CODEGRAPH_EXTRACTOR_FAILED`.

### 2.6. Чекпоинты

- При включённых чекпоинтах перед пользовательским промптом (`UserPromptSubmit`) снимается
  чекпоинт рабочей копии; для Codex — после успешной проверки hooks. Защитный снимок перед
  восстановлением (`safety`) снимается даже при выключенных чекпоинтах.
- Снимок делается приватным индексом: `read-tree`, `add -A` (с исключением `.aconsole/lock`),
  `write-tree`, `commit-tree`, `update-ref`. `HEAD`, обычный индекс и ignored-файлы не
  меняются; `git stash` и `git reset --hard` не применяются. Автор снимка —
  `AConsole <aconsole@localhost>`, чтобы не требовать `user.name` у пользователя.
- Legacy-чекпоинты вкладки лежат на `refs/aconsole/checkpoints/<tab>/<n>`; история сессии —
  на `refs/aconsole/sessions/<sid>/checkpoints/<seq>`. Обе схемы читаются.
- В истории сессии хранятся снимки видов `begin`, `checkpoint`, `safety`, `result`,
  `handoff`, `close-tab`, `restart`, `archive`, `end`, `recovery`, `before-restore`,
  а также записи Git-операций (`merge`, `rebase`, `cherry-pick`, `cleanup`). Поля снимка:
  `sequence`, `kind`, `actor_tab_id`, `worktree`, `ref`, `head_oid`, `oid`,
  `previous_oid`, `label`, `status`, `created_at`, `actor_title`, `agent`, `plan_node`.
- Список показывает номер, время, label, число файлов и вставок/удалений.
- Восстановление требует preview и одноразовый confirm-токен; перед ним создаётся
  `safety`-снимок. Затрагиваются отслеживаемые и новые файлы, но не ignored.
- Восстановление через историю сессии сначала останавливает исполнителей сессии,
  сохраняет защитный снимок и не меняет `HEAD`/индекс.
- Хранение: настройка `checkpoints_keep` (5…500, по умолчанию 100) использует
  периодическая уборка — она удаляет старые строки legacy-чекпоинтов вкладки и их refs,
  кроме refs, на которые ссылаются снимки сессий. Список в памяти ядра создаётся со
  значением по умолчанию (100) и настройку не перечитывает. История сессий автоматически
  не чистится.

### 2.7. Git

Страница Git содержит:

- обзор проекта и выбор сессии, участники и история этапов;
- состояние worktree и веток, ahead/behind, staged/unstaged/untracked/conflicted;
- граф коммитов и чекпоинтов с привязкой к плану, агенту и вкладке;
- diff по файлам и хункам, stage/unstage, commit;
- восстановление файла из коммита с preview и confirm-токеном;
- preview слияния (`git merge-tree --write-tree`, нужен Git ≥ 2.38) и режимы
  `fast_forward`, `merge`, `squash`;
- rebase, cherry-pick, `continue`/`abort` незавершённой операции;
- удаление worktree с отдельным подтверждением для ignored-файлов;
- отправку diff другому агенту во вкладку (preview + подтверждение);
- список фактов, которые стоит перепроверить после изменений кода.

Коммиты, сделанные через AConsole, получают trailers `AConsole-Session`, `AConsole-Node`,
`AConsole-Agent`, `AConsole-Tab`; финальное слияние сессии с несколькими участниками
пишет `AConsole-Participants`, `AConsole-Agents`, `AConsole-Nodes`, не назначая последнего
агента автором всей задачи. Коммиты внешнего CLI допустимы без trailers, точное авторство
строк из Git не выводится.

Слияние проверяет, что `source`/`target` не изменились с момента preview; устаревший
preview отвергается (`MERGE_PREVIEW_STALE`). Merge пишется в журнал `git_operations` со
статусами `started`, `conflicted`, `merged`, `finished`, `failed`, `aborted`.

Все обращения к Git идут через один runner (`infrastructure/git/runner.py`):

- argv-списки, `LC_ALL=C`, таймаут 30 с, не более 4 процессов, ответ ≤16 МБ;
- чтения выставляют `GIT_OPTIONAL_LOCKS=0`, чтобы фоновая проверка не спорила с агентом
  за index lock;
- каждая команда перекрывает конфигурацию репозитория: `core.fsmonitor=false`,
  `core.sshCommand=true`, пустые `diff.external`, `core.askPass`, `credential.helper`,
  `core.gitProxy`, `core.editor`, `sequence.editor`, `protocol.ext.allow=never`,
  отключённые gpg-подписи и `log.showSignature=false`;
- найденные в конфиге драйверы (`filter.*`, `diff.*.command`, `merge.*.driver`,
  `core.fsmonitor` и т. п.) перекрываются для конкретного процесса;
- для status/diff/снимков `core.hooksPath=/dev/null`; hooks явных `commit`/`merge`
  разрешаются осознанно и берутся из каталога `hooks` репозитория;
- diff всегда с `--no-ext-diff` и `--no-textconv`.

### 2.8. Лимиты и расходы

| Агент | Окна (`QuotaWindowView.key`) | Деньги |
|---|---|---|
| Claude | `5h`, `7d`, `budget` (extra usage) | стоимость сессии |
| Codex | `5h`, `7d`, `budget` (spend limit) | баланс кредитов или unlimited |
| Cursor | `auto`, `api`, `month`, `budget` (on-demand pool) | потрачено/лимит пула |
| OpenCode Go | `5h`, `week`, `month` | — |

Источники и период опроса (`AccountView.source`, `refresh_s`):

| Агент | Источник | Период |
|---|---|---|
| Claude | `/usage` внутри контейнера (`claude -p /usage --no-session-persistence`) | 60 с |
| Codex | `account/rateLimits/read` через `codex app-server` | 120 с |
| Cursor | dashboard API Cursor | 120 с |
| OpenCode Go | usage API OpenCode | 120 с |

Для Cursor и OpenCode используются те же endpoint'ы, что и в их dashboard: это не
публичные API, формат может измениться. Токены по моделям Claude и Codex читаются из
локальных session-логов; хранятся только числа и имена моделей.

Уровни (`QuotaLevel`): `ok`, `warn` (≥80 %), `critical` (≥95 %), `unknown` (нет свежего
наблюдения или истёк `resets_at`). UI дополнительно умеет показывать «использовано» или
«осталось» (`quota_display`).

### 2.9. Настройки и проверка окружения

Контракт `Settings` (18 полей):

| Поле | Тип / диапазон | По умолчанию |
|---|---|---|
| `rev` | int | — |
| `vault_path` | str | `""` → `~/Obsidian/AConsole` |
| `worktrees_dir` | str | `""` → `$XDG_DATA_HOME/aconsole/worktrees` |
| `approvals_mode` | `inline` / `native` | `inline` |
| `approval_hold_seconds` | 30…540 | 540 |
| `notify_when` | `unfocused` / `always` / `never` | `unfocused` |
| `terminal_font` | str \| none | none (системный) |
| `terminal_font_size` | 8…32 | 11 |
| `scrollback_lines` | 1 000…200 000 | 20 000 |
| `theme` | `system` / `light` / `dark` | `system` |
| `quota_warn_pct` | 0…100 | 80 |
| `quota_critical_pct` | 0…100 | 95 |
| `checkpoints_enabled` | bool | true |
| `checkpoints_keep` | 5…500 | 100 |
| `keymap_overrides` | dict[str, str] | `{}` |
| `codegraph_languages` | набор | `csharp`, `python`, `rust`, `typescript` |
| `setup_completed` | bool | false |
| `quota_display` | `used` / `remaining` | `used` |

Запись идёт через `settings/update` с проверкой `rev` (`CONTENT_MODIFIED` при расхождении);
одинаковый патч не увеличивает `rev`. Диалог настроек в UI в 0.1.1 редактирует семь полей:
шрифт и его размер, scrollback, тему, отображение квот, режим согласований и время
удержания. Остальные поля хранятся и читаются ядром/UI там, где это реализовано
(см. §12.4 о фактическом применении).

Мастер первого запуска (`setup/checks`) проверяет восемь пунктов:

1. `podman` и `distrobox` в PATH;
2. контейнеры `agent-*` (`podman ps -a`);
3. логин (только Claude проверяется автоматически);
4. версии CLI в контейнерах;
5. каталог vault;
6. монтирование worktree и vault в контейнеры;
7. инструменты `devtools` (Python, Cargo, .NET, Node);
8. `notify-send` и сессионный D-Bus.

Исправления (`setup/previewFix`, `setup/applyFix`) требуют preview, confirm-токена и
повторной проверки. В 0.1.1 автоматических исправлений два: `create_storage_directory`
и `create_worktree_directory`. Есть диалог здоровья ядра: очередь writer, ошибки writer,
время последнего коммита, число и худшая задержка IPC, resync.

---

## 3. Архитектура

### 3.1. Два процесса на двух Python

Ядро требует free-threaded Python (3.15t), GTK под ним не загружается. Поэтому AConsole
запускает два процесса:

| Процесс | Окружение | Python | Что внутри |
|---|---|---|---|
| UI | `.venv-ui` (в пакете `/opt/aconsole/ui`) | 3.15 с GIL | GTK 4, libadwaita, VTE, типизированный клиент |
| Ядро | `.venv-core` (в пакете `/opt/aconsole/core`) | 3.15t без GIL | сервисы, SQLite, Git, Podman, HTTP ingest, MCP |

Ядро отказывается стартовать с включённым GIL и печатает список кандидатов расширений;
обход — флаг `--allow-gil` (только для отладки). UI находит интерпретатор ядра через
`ACONSOLE_CORE_PYTHON` или соседний `.venv-core`/`core`.

### 3.2. Транспорт и файлы

| Что | Путь |
|---|---|
| Unix-сокет IPC | `$XDG_RUNTIME_DIR/aconsole/core.sock`, иначе `~/.local/state/aconsole/core.sock` |
| Lock ядра | рядом с сокетом: `core.lock` (эксклюзивный `flock`, non-blocking) |
| База SQLite | `$XDG_DATA_HOME/aconsole/aconsole.db`, иначе `~/.local/share/aconsole/aconsole.db` |
| Рабочие копии | `$XDG_DATA_HOME/aconsole/worktrees/<repo>/<tab>` |
| Лог ядра | `$XDG_STATE_HOME/aconsole/core.log`, иначе `~/.local/state/aconsole/core.log` (хранятся два последних запуска) |
| Vault по умолчанию | `~/Obsidian/AConsole` |

- Транспорт — NDJSON (JSON-RPC 2.0) поверх Unix-сокета.
- Второй экземпляр ядра получает отказ по lock (`EXIT_REFUSED`).
- Ядро поднимается по требованию и завершается после простоя (`IDLE_EXIT_S = 15 с`),
  когда нет ни одного удерживающего клиента (`ui`/`cli`) и ни одной живой вкладки.
  Настоящее ядро переживает закрытие окна (`keep_daemon`), поэтому новое окно находит его;
  fake core живёт ровно столько, сколько его UI.
- UI и CLI принимаются только из mount namespace ядра; процесс из контейнера может
  подключиться только как sidecar.

### 3.3. Слои и правила зависимостей

`pyproject.toml` описывает контракты import-linter:

| Правило | Смысл |
|---|---|
| UI не импортирует core | `aconsole.ui` → запрещён `aconsole.core` |
| Core не импортирует UI и `gi` | `aconsole.core` → запрещены `aconsole.ui`, `gi` |
| Domain без инфраструктурных библиотек | запрещены SQLAlchemy, msgspec, mcp, uvicorn, watchdog, subprocess |
| Application без infrastructure | `core.application` → запрещён `core.infrastructure` |
| Contracts не импортируют остальной AConsole | запрещены core, devcli, sidecar, ui |
| Sidecar только на стандартной библиотеке | запрещены contracts, core, devcli, ui, gi, mcp, msgspec, sqlalchemy, uvicorn, watchdog |

Типы проверяются basedpyright в режиме `all` (`typeCheckingMode = "all"`,
`failOnWarnings = true`), `typing.Any` и `typing.cast` запрещены на уровне ruff,
raw `sqlalchemy.text()` разрешён только в `persistence/engine.py`.

### 3.4. Структура репозитория

| Путь | Содержимое |
|---|---|
| `src/aconsole/contracts` | msgspec-контракты, таблица методов, ошибки, события, схемы, генераторы |
| `src/aconsole/core/domain` | чистые правила: квоты, план, риск, approvals, checkpoints, Git-состояние, work session |
| `src/aconsole/core/application` | сервисы и порты: вкладки, проекты, сессии, Git, review, квоты, setup |
| `src/aconsole/core/infrastructure` | Podman, Distrobox, Git, SQLite, HTTP ingest, MCP, vault, codegraph, агенты, fake core |
| `src/aconsole/ui` | GTK: views, viewmodels, logic, client |
| `src/aconsole/sidecar` | PTY-помощник (stdlib) |
| `src/aconsole/devcli` | CLI-клиент (`snapshot`, `events`) и проверка соответствия MCP |
| `migrations` | Alembic (`0001`…`0006` в 0.1.1) |
| `contracts/schema` | сгенерированные JSON Schema и OpenAPI для ingest (251 файл) |
| `packaging` | `.desktop`, иконки, `build_deb.py` |
| `tools` | `check.sh`, gate, hostrun, codegraph, бенчмарки |
| `tests` | core, contracts, gui, ui, smoke, gate, hostrun, unit |
| `plans`, `artifacts` | планы версий и отчёты приёмки |
| `spikes`, `stubs` | исследовательские прототипы и заглушки типов |
| `.github/workflows/check.yml` | CI на Ubuntu 24.04 |
| `install.sh` | сборка двух окружений и ярлык |

---

## 4. Контракты

`src/aconsole/contracts` — единственное, что разделяют UI и ядро. Из таблицы методов и
msgspec-структур генерируются типизированный клиент UI, JSON Schema и OpenAPI для ingest,
поэтому UI и ядро не могут незаметно разойтись в названиях методов и полей.

### 4.1. Протокол IPC

| Параметр | Значение |
|---|---|
| Формат | NDJSON, JSON-RPC 2.0 |
| Версия | `1.0` |
| Максимальный размер сообщения | 16 МиБ (`ProtocolLimits.max_message_bytes`) |
| Кольцо событий | 4 096 (`event_ring_size`) |
| Одновременных запросов | 64 (`max_inflight_requests`) |
| Исходящая очередь событий на соединение | 1 024; переполнение → `RESYNC_REQUIRED` |
| Роли | `ui`, `cli`, `sidecar` |

Конверты: `Request` (`kind=request`, `jsonrpc`, `id`, `method`, `params`, `timeout_ms`),
`Notification` (`kind=notification`), `ResponseOk` (`kind=result`), `ResponseErr`
(`kind=error`). События приходят как `Notification` с `EventEnvelope` (`seq`, `at`, `event`).

Handshake `initialize`: клиент присылает диапазон версий протокола, роль, имя и версию
клиента, pid, набор capability, локаль, `tab_id`, `sidecar_token` (для sidecar). Ядро
возвращает выбранную версию, `server_version`, `server_pid`, `session_id`,
пересечение capability и лимиты.

| Возможности ядра (`_SERVER_CAPS`) | Возможности UI (`_UI_CAPABILITIES`) |
|---|---|
| `events.v1` | `events.v1` |
| `approvals.inline` | `approvals.inline` |
| `notifications.desktop` | `terminal.inject` |
| `diff.v1` | `notifications.desktop` |
| `checkpoints.v1` | `diff.v1` |
| `git.v1` | `checkpoints.v1` |
| `work_sessions.v1` | `work_sessions.v1` |
| `terminal.inject` | — |

События согласований доставляются только при `approvals.inline`.

### 4.2. Методы (81)

Столбец «capability» — требование к возможностям клиента, объявленное в контракте
(`Method.capability`). В 0.1.1 ядро не проверяет его при диспетчеризации: поле попадает
в сгенерированную документацию и в клиент, а фактически проверяется только
`approvals.inline` (доставка событий согласований и их наличие в snapshot) и
`work_sessions.v1` в handshake UI. «long» — метод отнесён к длительным: клиент берёт для
него таймаут 60 с вместо 10 с. Все методы обслуживаются ядром, кроме отмеченных в §12.5.

#### Жизненный цикл и приложение

| Метод | Сигнатура | Роли |
|---|---|---|
| `initialize` | `InitializeParams` → `InitializeResult` | ui, cli, sidecar |
| `shutdown` | `EmptyParams` → `EmptyResult` | ui, cli, sidecar |
| `app/snapshot` | `EmptyParams` → `SnapshotResult` | ui, cli |
| `app/health` | `EmptyParams` → `HealthResult` | ui, cli |
| `app/quitCheck` | `EmptyParams` → `QuitCheckResult` | ui, cli |
| `app/stopAgents` | `EmptyParams` → `EmptyResult` | ui, cli, long |
| `events/subscribe` | `SubscribeParams` → `SubscribeResult` | ui, cli |
| `events/unsubscribe` | `EmptyParams` → `EmptyResult` | ui, cli |
| `quota/refresh` | `EmptyParams` → `EmptyResult` | ui, cli |
| `settings/get` | `EmptyParams` → `Settings` | ui, cli |
| `settings/update` | `SettingsUpdateParams` → `Settings` | ui, cli |

#### Проекты

| Метод | Сигнатура | Ошибки |
|---|---|---|
| `project/add` | `ProjectAddParams` → `ProjectView` | `PROJECT_NOT_A_GIT_REPO`, `PATH_NOT_ALLOWED` |
| `project/remove` | `ProjectIdParams` → `EmptyResult` | `TAB_STATE_CONFLICT` |
| `project/setActive` | `ProjectIdParams` → `EmptyResult` | `PROJECT_NOT_FOUND` |

#### Вкладки и терминал

| Метод | Сигнатура | Примечание |
|---|---|---|
| `tab/open` | `OpenTabParams` → `TabView` | long; `PROJECT_NOT_FOUND`, `AGENT_UNAVAILABLE`, `AGENT_NOT_LOGGED_IN`, `WORKTREE_CREATE_FAILED`, `BRANCH_ALREADY_CHECKED_OUT`, `SESSION_NOT_FOUND` |
| `tab/launchSpec` | `TabIdParams` → `TerminalLaunchSpec` | argv sidecar, env, cwd, scrollback, title |
| `terminal/prepareImagePaste` | `PrepareImagePasteParams` → `PreparedImagePaste` | long, только роль `ui` |
| `terminal/screenTextReply` | `ScreenTextReplyParams` → `EmptyResult` | capability `terminal.screen_text` |
| `tab/started` | `StartedParams` → `EmptyResult` | pid процесса |
| `tab/exited` | `ExitedParams` → `EmptyResult` | wait status |
| `tab/close` | `CloseTabParams` → `CloseTabResult` | long; `WORKTREE_DIRTY` |
| `tab/restart` | `RestartParams` → `TabView` | long; `resume` |
| `tab/fork` | `TabIdParams` → `TabView` | только для агентов с `fork` |
| `tab/rename` | `RenameParams` → `EmptyResult` | — |
| `tab/reorder` | `ReorderParams` → `EmptyResult` | — |
| `tab/focus` | `FocusParams` → `EmptyResult` | — |

#### Согласования

| Метод | Сигнатура | Ошибки |
|---|---|---|
| `approval/decide` | `DecideParams` → `AppliedResult` | `APPROVAL_NOT_FOUND`, `APPROVAL_EXPIRED`, `APPROVAL_NONCE_MISMATCH`, `APPROVAL_ALREADY_DECIDED` |
| `approval/releaseToNative` | `ReleaseParams` → `ReleasedResult` | `APPROVAL_NOT_FOUND`, `APPROVAL_ALREADY_DECIDED`, `APPROVAL_NONCE_MISMATCH` |

#### Рабочие сессии (`work_sessions.v1`)

| Метод | Сигнатура | long |
|---|---|---|
| `workSession/create` | `WorkSessionCreateParams` → `WorkSessionView` | — |
| `workSession/list` | `WorkSessionListParams` → `WorkSessionListResult` | — |
| `workSession/get` | `WorkSessionParams` → `WorkSessionView` | — |
| `workSession/rename` | `WorkSessionRenameParams` → `WorkSessionView` | — |
| `workSession/close` | `WorkSessionParams` → `WorkSessionView` | — |
| `workSession/resume` | `WorkSessionParams` → `WorkSessionView` | — |
| `workSession/handoff` | `WorkSessionHandoffParams` → `TabView` | да |
| `workSession/history` | `WorkSessionParams` → `WorkSessionHistoryResult` | да |
| `workSession/restorePreview` | `WorkSessionRestorePreviewParams` → `WorkSessionRestorePreview` | да |
| `workSession/restore` | `WorkSessionRestoreParams` → `WorkSessionHistoryResult` | да |
| `workSession/result` | `WorkSessionResultParams` → `WorkSessionHistoryResult` | да |
| `workSession/integratePreview` | `WorkSessionIntegratePreviewParams` → `MergePreviewResult` | да |
| `workSession/integrate` | `WorkSessionIntegrateParams` → `GitOperationResult` | да |
| `workSession/cleanupWorker` | `WorkSessionCleanupWorkerParams` → `EmptyResult` | да |

#### Планы, знания, vault

| Метод | Сигнатура | capability |
|---|---|---|
| `plan/list` | `PlanListParams` → `PlanListResult` | — |
| `plan/get` | `PlanIdParams` → `PlanView` | `PLAN_NOT_FOUND` |
| `plan/setNodeStatus` | `SetNodeStatusParams` → `PlanNodeView` | — |
| `knowledge/impact` | `ImpactParams` → `ImpactView` | long |
| `knowledge/noteUri` | `NoteUriParams` → `NoteUriResult` | `NOTE_NOT_FOUND` |

#### Чекпоинты и diff

| Метод | Сигнатура | capability |
|---|---|---|
| `checkpoint/list` | `CheckpointListParams` → `CheckpointListResult` | `checkpoints.v1` |
| `checkpoint/restorePreview` | `CheckpointIdParams` → `RestorePreviewResult` | `checkpoints.v1` |
| `checkpoint/restore` | `RestoreParams` → `RestoreResult` | `checkpoints.v1`, long |
| `diff/summary` | `DiffSummaryParams` → `DiffSummaryResult` | `diff.v1` |
| `diff/file` | `DiffFileParams` → `DiffFileResult` | `diff.v1` |
| `diff/sendComment` | `SendCommentParams` → `CommandResult` | `diff.v1` |

#### Git (`git.v1`, 21 метод)

| Метод | Сигнатура | long |
|---|---|---|
| `git/state` | `GitStateParams` → `GitStateResult` | — |
| `git/watch` | `GitWatchParams` → `EmptyResult` | — |
| `git/graph` | `GitGraphParams` → `GitGraphResult` | — |
| `git/diffSummary` | `GitDiffParams` → `DiffSummaryResult` | — |
| `git/diffFile` | `GitDiffFileParams` → `DiffFileResult` | — |
| `git/stage` | `GitPathsParams` → `GitChangeResult` | — |
| `git/unstage` | `GitPathsParams` → `GitChangeResult` | — |
| `git/commit` | `GitCommitParams` → `GitChangeResult` | да |
| `git/restoreFilePreview` | `RestoreFilePreviewParams` → `RestoreFilePreviewResult` | — |
| `git/restoreFile` | `RestoreFileParams` → `GitChangeResult` | — |
| `git/mergePreview` | `MergePreviewParams` → `MergePreviewResult` | — |
| `git/mergeWorktree` | `MergeWorktreeParams` → `GitOperationResult` | да |
| `git/operation` | `GitOperationParams` → `GitOperationResult` | — |
| `git/cleanupWorktree` | `CleanupWorktreeParams` → `CleanupWorktreeResult` | да |
| `git/factsToReview` | `GitStateParams` → `FactsToReviewResult` | — |
| `git/rebase` | `RebaseParams` → `GitOperationResult` | да |
| `git/cherryPick` | `CherryPickParams` → `GitOperationResult` | да |
| `git/continue` | `WorktreeOperationParams` → `GitChangeResult` | да |
| `git/abort` | `WorktreeOperationParams` → `GitChangeResult` | да |
| `git/sendDiffPreview` | `SendDiffPreviewParams` → `SendDiffPreviewResult` | — |
| `git/sendDiff` | `SendDiffParams` → `CommandResult` | — |

#### Настройка окружения

| Метод | Сигнатура | long |
|---|---|---|
| `setup/checks` | `EmptyParams` → `ChecksResult` | да |
| `setup/previewFix` | `FixIdParams` → `FixPreview` | — |
| `setup/applyFix` | `ApplyFixParams` → `CheckResult` | да |

#### Палитра и нативные сессии

| Метод | Сигнатура | Статус в 0.1.1 |
|---|---|---|
| `palette/items` | `PaletteQuery` → `PaletteItemsResult` | только fake core |
| `palette/execute` | `PaletteExecuteParams` → `CommandResult` | только fake core |
| `palette/resetRanking` | `ResetRankingParams` → `EmptyResult` | только fake core |
| `session/list` | `SessionListParams` → `SessionListResult` | только fake core |

Итого 24 метода помечены `long`, 42 — требуют capability.

### 4.3. События UI (22)

| Тег | Полезная нагрузка |
|---|---|
| `tab_added` | `tab: TabView` |
| `tab_updated` | `tab: TabView`, `changed: frozenset[str]` |
| `tab_removed` | `tab_id` |
| `tab_focus_requested` | `tab_id` |
| `approval_requested` | `approval: ApprovalView` |
| `approval_resolved` | `approval_id`, `outcome` (`allowed`/`denied`/`expired_to_native`/`cancelled`), `by` (`user`/`rule`/`agent`/`timeout`) |
| `quota_updated` | `account: AccountView` |
| `plan_updated` | `plan: PlanView` |
| `projects_changed` | `projects`, `active_project` |
| `agents_changed` | `agents: tuple[AgentInfo, ...]` |
| `settings_changed` | `rev` |
| `notify` | `tab_id`, `title`, `body`, `priority`, `episode_id` |
| `toast` | `message`, `action_label`, `action_item_id`, `timeout_s` |
| `banner` | `id`, `message`, `button_label`, `button_item_id`, `visible` |
| `inject_to_terminal` | `tab_id`, `text`, `submit` |
| `checkpoint_created` | `checkpoint: CheckpointView` |
| `git_state_updated` | `project_id`, `revision` |
| `operation_progress` | `operation_id`, `title`, `fraction`, `done`, `error` |
| `vault_edit_rejected` | `note` |
| `screen_text_requested` | запрос текста экрана у sidecar |
| `resync_required` | `reason` |
| `setup_required` | требуется мастер настройки |

События нумеруются в кольце (`seq`) и приходят как JSON-RPC-уведомление `$/event`
с `EventEnvelope` (`seq`, `at`, `event`). При переполнении исходящей очереди клиент
получает `resync_required` и обязан перечитать состояние через `app/snapshot`.

### 4.4. Коды ошибок (77)

Стандартные JSON-RPC и протокол:

| Код | Имя |
|---|---|
| -32700 | `PARSE_ERROR` |
| -32600 | `INVALID_REQUEST` |
| -32601 | `METHOD_NOT_FOUND` |
| -32602 | `INVALID_PARAMS` |
| -32603 | `INTERNAL_ERROR` |
| -32001 | `NOT_INITIALIZED` |
| -32002 | `ALREADY_INITIALIZED` |
| -32003 | `PROTOCOL_VERSION_UNSUPPORTED` |
| -32004 | `MESSAGE_TOO_LARGE` |
| -32005 | `SERVER_BUSY` |
| -32006 | `RESYNC_REQUIRED` |
| -32007 | `SIDECAR_TOKEN_INVALID` |
| -32008 | `ROLE_FORBIDDEN` |
| -32800 | `REQUEST_CANCELLED` |
| -32801 | `CONTENT_MODIFIED` |
| -32802 | `SERVER_CANCELLED` |

Ingest:

| Код | Имя |
|---|---|
| 1001 | `INGEST_UNAUTHORIZED` |
| 1002 | `INGEST_TAB_MISMATCH` |
| 1003 | `INGEST_PAYLOAD_INVALID` |
| 1004 | `INGEST_ORIGIN_FORBIDDEN` |

Проекты, вкладки, контейнеры:

| Код | Имя |
|---|---|
| 2001 | `PROJECT_NOT_FOUND` |
| 2002 | `PROJECT_NOT_A_GIT_REPO` |
| 2003 | `TAB_NOT_FOUND` |
| 2004 | `TAB_STATE_CONFLICT` |
| 2005 | `AGENT_UNAVAILABLE` |
| 2006 | `AGENT_NOT_LOGGED_IN` |
| 2007 | `CONTAINER_START_FAILED` |
| 2008 | `WORKTREE_CREATE_FAILED` |
| 2009 | `WORKTREE_DIRTY` |
| 2010 | `BRANCH_ALREADY_CHECKED_OUT` |
| 2011 | `PATH_NOT_ALLOWED` |
| 2012 | `SESSION_NOT_FOUND` |

Согласования, чекпоинты, Git, сессии:

| Код | Имя |
|---|---|
| 3001 | `APPROVAL_NOT_FOUND` |
| 3002 | `APPROVAL_EXPIRED` |
| 3003 | `APPROVAL_NONCE_MISMATCH` |
| 3004 | `APPROVAL_ALREADY_DECIDED` |
| 3005 | `CHECKPOINT_NOT_FOUND` |
| 3006 | `CHECKPOINT_RESTORE_CONFLICT` |
| 3007 | `CONFIRM_TOKEN_INVALID` |
| 3008 | `DIFF_TOO_LARGE` |
| 3020 | `GIT_COMMAND_FAILED` |
| 3021 | `GIT_WORKTREE_UNKNOWN` |
| 3022 | `GIT_BRANCH_UNKNOWN` |
| 3023 | `GIT_SNAPSHOT_STALE` |
| 3024 | `GIT_STATE_STALE` |
| 3025 | `WORKTREE_BUSY` |
| 3026 | `GIT_OPERATION_IN_PROGRESS` |
| 3027 | `GIT_OPERATION_NOT_FOUND` |
| 3028 | `MERGE_PREVIEW_STALE` |
| 3029 | `MERGE_CONFLICT` |
| 3030 | `MERGE_MODE_UNAVAILABLE` |
| 3031 | `WORKTREE_NOT_REMOVABLE` |
| 3032 | `DATABASE_TRANSACTION_FAILED` |
| 3040 | `WORK_SESSION_NOT_FOUND` |
| 3041 | `WORK_SESSION_ARCHIVED` |
| 3042 | `WORK_SESSION_WORKSPACE_CONFLICT` |

Планы и знания:

| Код | Имя |
|---|---|
| 4001 | `PLAN_NOT_FOUND` |
| 4002 | `NODE_NOT_FOUND` |
| 4003 | `PLAN_CYCLE` |
| 4004 | `NODE_TRANSITION_INVALID` |
| 4005 | `NODE_NOT_READY` |
| 4006 | `LEASE_HELD` |
| 4007 | `LEASE_STALE_FENCE` |
| 4008 | `LEASE_EXPIRED` |
| 4009 | `NODE_EVIDENCE_REQUIRED` |
| 4010 | `PATHS_OVERLAP` |

Vault и codegraph:

| Код | Имя |
|---|---|
| 5001 | `VAULT_NOT_CONFIGURED` |
| 5002 | `VAULT_WRITE_DEFERRED` |
| 5003 | `VAULT_CONFLICT` |
| 5004 | `VAULT_EDIT_REJECTED` |
| 5005 | `NOTE_NOT_FOUND` |
| 5006 | `CODEGRAPH_EXTRACTOR_FAILED` |
| 5007 | `IMPACT_TARGET_UNKNOWN` |

Настройка и прочее:

| Код | Имя |
|---|---|
| 6001 | `SETUP_FIX_UNKNOWN` |
| 6002 | `SETUP_FIX_FAILED` |
| 6003 | `AGENT_CONFIG_UNSUPPORTED_VERSION` |
| 9001 | `UNEXPECTED` |

Ошибка несёт `code`, `message` (имя), необязательный `data` с типизированными details
(`ErrorData`) — например `WorktreeDirtyDetails`, `LeaseStaleFenceDetails`,
`PathNotAllowedDetails`. Флаг `retryable` подсказывает UI, стоит ли повторять вызов.

---

## 5. Модель данных

SQLite, SQLAlchemy 2.1, Alembic. Запись идёт через отдельную очередь writer, чтение —
через пул; `PRAGMA journal_mode=WAL`, `PRAGMA busy_timeout=5000`, `timeout=5.0`.

### 5.1. Таблицы (27)

| Группа | Таблицы |
|---|---|
| Проекты и рабочие копии | `projects`, `workspaces`, `work_sessions`, `work_session_snapshots` |
| Вкладки и диалоги | `tabs`, `agent_sessions` |
| Планы | `plans`, `plan_nodes`, `plan_edges`, `leases`, `node_artifacts`, `canvas_positions` |
| Знания и vault | `kb_nodes`, `kb_edges`, `vault_files`, `handovers`, `iterations` |
| Согласования | `approvals`, `approval_rules` |
| Git | `checkpoints`, `git_operations` |
| Лимиты | `agent_accounts`, `quota_current`, `quota_history` |
| Прочее | `events`, `frecency`, `settings` |

### 5.2. Миграции 0.1.1

| Ревизия | Что добавляет |
|---|---|
| `0001_initial` | начальная схема, DDL зафиксирован в миграции |
| `0002_plan_node_details` | границы, формат ответа и заметка узла плана |
| `0003_knowledge_details` | тело факта и состав handover |
| `0004_git_operations` | журнал merge/rebase/cherry-pick |
| `0005_work_sessions` | рабочие сессии; существующие workspace/вкладки переносятся без Git-операций |
| `0006_session_history` | снимки сессии с акторами |

Миграции применяются при старте ядра до открытия сокета: UI ждёт готовности до 20 секунд.

### 5.3. Хранение и очистка

- Writer-очередь сериализует записи; её состояние видно в `app/health`
  (`writer_queued`, `writer_failed`, `writer_last_commit_ms`) и в диалоге здоровья.
- Периодическая уборка (`persistence/cleanup.py`, при старте и раз в час):
  - `events` — не более 2 000 записей на вкладку;
  - `quota_history` — 30 дней;
  - `approvals` — 90 дней;
  - закрытые вкладки — 30 дней;
  - legacy-чекпоинты — по `checkpoints_keep` (5…500, по умолчанию 100).
- История сессий автоматически не удаляется.

---

## 6. Ядро

### 6.1. Domain

Чистые правила без инфраструктурных библиотек:

| Модуль | Отвечает за |
|---|---|
| `agent.py` | `AgentKind`, матрица `AgentCapabilities` |
| `approvals.py` | жизненный цикл запроса: nonce, hash входа, дедупликация, срок, точные правила |
| `prefix_allowlist.py` | allowlist пар «программа + подкоманда», `ALLOWLIST_VERSION = 2`, `GIT_ENV` |
| `risk.py` | классификация shell-команд в шесть риск-тегов |
| `plan.py` | DAG плана, переходы статусов, lease/fence, `after_merge` |
| `checkpoints.py` | имена side-refs, ограничения `keep` |
| `git_state.py` | что факты Git значат для вкладок; `blocks_writes`, `require_oid` |
| `git_operation.py` | запись операции и trailers коммита |
| `quota.py` | `QuotaLevel`, слияние наблюдений, пороги |
| `tab_status.py` | статусы вкладки и их переходы |
| `workspace.py` | имена веток/каталогов, slug, решение о закрытии |
| `work_session.py` | `WorkSession`, `WorkSessionSnapshot`, статусы |
| `vault_sync.py`, `events.py`, `errors.py`, `ids.py`, `clock.py` | вспомогательные правила |

### 6.2. Application

Сервисы (все вызываются на одном tab-worker, кроме явно асинхронных):

| Сервис | Сценарии |
|---|---|
| `projects.py` | каталог проектов, базовая ветка, активный проект |
| `tabs.py` | открытие/закрытие/перезапуск/форк вкладок, эстафета, настройки, dispatch `TAB_METHODS` |
| `work_sessions.py` | книга сессий, конфликт workspace, архив/возобновление |
| `work_session_history.py` | снимки: `prepare` → `complete`, восстановление, акторы |
| `work_session_git.py` | `result` → `integratePreview` → `integrate` → `cleanupWorker` |
| `approval_broker.py` | удержание запроса до решения или таймаута, таймеры, release to native |
| `checkpoints.py` | `on_user_prompt`, list/get/preview/restore, retention |
| `diff.py` | сводка и файл diff, комментарий агенту |
| `review.py` | dispatch `checkpoint/*` и `diff/*`, автоснимки перед промптом |
| `git.py` | dispatch 21 метода `git/*`, наблюдение за изменениями |
| `git_state.py` | состояние worktree, граф, refs, trailers |
| `git_ops.py`, `git_merge.py`, `git_rewrite.py`, `git_cleanup.py` | операции, слияния, rebase/cherry-pick, очистка |
| `quota_aggregator.py` | слияние наблюдений по аккаунту |
| `setup.py` | проверки и исправления окружения |
| `event_router.py`, `coalescer.py` | маршрутизация и склейка событий |

Порты (`application/ports`): `checkpoint_store`, `clock`, `diff_reader`, `events`,
`git_repo`, `knowledge_store`, `plan_store`, `setup`, `tab_store`, `work_session_store`.

### 6.3. Infrastructure

| Модуль | Что делает |
|---|---|
| `agents/` | адаптеры Claude, Codex, OpenCode, Cursor: launcher, limits, sessions, hooks, skills, usage-log |
| `containers/` | Podman (inspect, start, exec argv, orphan scan), Distrobox env/ini, login shell |
| `git/` | runner, worktrees, checkpoints, history, operations, diff, watch |
| `persistence/` | engine, models, writer, uow, repositories, cleanup, startup |
| `http/` | ingest-приложение: auth, routes, consumer, problem+json |
| `ipc/` | framing, session, ring |
| `mcp/` | сервер `/mcp` и двенадцать инструментов |
| `vault/` | sync (frontmatter, трёхсторонний merge) и writer |
| `codegraph/` | индексация Python/TS/Rust/C#, экстракторы в `devtools` |
| `fake/` | fake core: 8 сценариев, ответы на RPC |
| `tabs.py` | host-адаптеры вкладок: Podman, worktree, login-файлы, лимиты |
| `clipboard.py` | приём PNG (8 МиБ, 32 Мп) и приватный файл в рабочей копии |
| `setup_checks.py` | восемь проверок окружения и два исправления |
| `knowledge.py` | `PLAN_METHODS` + `knowledge/noteUri`, `knowledge/impact` |

### 6.4. HTTP ingest

Один ASGI-приложение на `127.0.0.1` со случайным портом (`port=0`), поднимается вместе с
ядром; MCP живёт на том же приложении по пути `/mcp`.

| Маршрут | Назначение |
|---|---|
| `GET /v1/health` | версия приложения |
| `POST /v1/tabs/<tab>/claude/hook` | hooks Claude |
| `POST /v1/tabs/<tab>/claude/statusline` | statusline Claude (стоимость, лимиты сессии) |
| `POST /v1/tabs/<tab>/codex/hook` | hooks Codex |
| `POST /v1/tabs/<tab>/codex/notify` | `notify` Codex |

Ограничения и защита:

- тело ≤ 1 МиБ (`MAX_BODY_BYTES`), проверяется и по `Content-Length`;
- у каждой вкладки свой bearer-токен (`TabTokens`), несовпадение вкладки — `INGEST_TAB_MISMATCH`;
- `Origin` разрешён отсутствующий, `null` и локальные `http(s)`-источники;
- очередь событий ограничена 10 000; при переполнении сохраняется последнее состояние;
- ошибки отдаются как `application/problem+json`;
- токены восстанавливаются между стартами ядра, чтобы старые hooks и MCP-сессии оставались
  валидными до явного перезапуска агента.

### 6.5. MCP

- Streamable HTTP на `/mcp`, `structured_output=False`, JSON-ответы, привязка к loopback.
- Bearer-токен вкладки определяет контекст (`TabContext`: проект, worktree, агент, вкладка);
  инструменты вызываются на loopback-приложении и не требуют отдельного порта.
- Ответы уплотняются (`compact`): без отступов, пустых полей и лишних схем; размер
  считается и передаётся в UI, чтобы видеть расход контекста агента.
- Инструменты идемпотентны там, где это заявлено: повтор `plan_create` с тем же названием
  возвращает тот же план, повтор `plan_add_nodes` не меняет существующие ключи,
  повтор `node_attach` возвращает тот же id.
- `node_update`/`node_attach` под активным lease требуют fence из `node_claim`;
  переход без lease может не передавать fence.

---

## 7. Интерфейс

### 7.1. Окно и навигация

- Окно libadwaita: заголовок, вкладки сверху, сайдбар (лимиты, план, Git) и область
  терминала; отдельные страницы — review/Git, палитра, мастер настройки, настройки,
  горячие клавиши, диалог здоровья, окно лимитов, карточка согласования.
- Сайдбар: `QuotaCard` (по аккаунтам и окнам), `PlanCard`/`PlanGraph` (DAG и прогресс),
  `GitCard` (ahead/behind, dirty).
- Вкладки сгруппированы по задачам; у вкладки есть статус, признак внимания, меню
  (переименовать, закрыть) и кнопка закрытия.
- Страница Git (`views/review`): панель сессии (`session_bar`), список файлов, diff по
  хункам с комментариями, действия (`git_actions`: stage/unstage/commit/merge/rebase/
  cherry-pick/restore/cleanup/send diff), граф (`graph_cell` + `logic/git_lanes`).
- Карточка согласования (`views/approvals/panel.py`) плавает над низом терминала, поэтому
  показ не меняет размер PTY и агент не перерисовывается.
- Диалог здоровья показывает: очередь writer, ошибки writer, время коммита, число
  отвеченных IPC-запросов, худшую задержку и число resync.

### 7.2. Клиент

`ui/client` — типизированный клиент, сгенерированный из контрактов: `methods.py`
(таблица вызовов), `typed_facade.py` (методы на каждый RPC), `core_client.py` (соединение,
handshake, capability, backoff 0.05→0.8 с, догон событий), `daemon.py` (запуск ядра,
поиск интерпретатора), `store.py` и `ui_state.py` (состояние), `error_text.py`
(человеческие тексты ошибок и путь к логу), `core_age.py` (проверяет, не новее ли код
ядра на диске, чем запущенный процесс ядра).

- UI сам поднимает ядро нужным Python и ждёт сокет до 20 секунд.
- При несовпадении версии/возможностей UI показывает объяснение, а не «неизвестную ошибку».
- Переопределения горячих клавиш применяются из `Settings.keymap_overrides`; неизвестное
  действие или коллизия отвергаются, сохранённое устаревшее действие `handover` игнорируется.

### 7.3. Локализация

Тексты собраны в словарях `messages.py` рядом с представлениями и выбираются по локали
(`ru`, иначе английский). Отдельного каталога `.po`/gettext в 0.1.1 нет.

### 7.4. Снимки

`ui/spike.py` и `tools/ui_snapshot.py` умеют рендерить окно и отдельные представления в
PNG (`window`, `tabs`, `plan-graph`, `review`, `git`, `quota-dialog`, `quota-account`,
`palette`, `setup`, `prefs`) для визуальных проверок и артефактов.

---

## 8. Sidecar

`python -I -m aconsole.sidecar -- <команда агента>` запускается UI-процессом; фактическая
команда — `podman exec` внутри контейнера.

| Переменная | Назначение |
|---|---|
| `ACONSOLE_SOCKET` | сокет ядра |
| `ACONSOLE_TAB` | идентификатор вкладки (маркер orphan scan) |
| `ACONSOLE_SIDECAR_TOKEN` | одноразовый токен sidecar |
| `ACONSOLE_PORT` | порт ingest (для hooks) |
| `ACONSOLE_SCREEN_WATCH` | включает наблюдение за экраном (Cursor) |

Что делает sidecar:

- создаёт PTY, копирует stdin в дочерний процесс, кадрирует вывод;
- передаёт `SIGWINCH` (размер), `SIGTERM`/`SIGHUP` в группу процессов;
- закрытый stdin (VTE исчез) → `SIGHUP` группе процессов, затем `SIGKILL` через 200 мс;
- шлёт в ядро NDJSON-уведомления `sidecar/started`, `sidecar/stats` (раз в 10 с),
  `sidecar/exited`, `sidecar/signal`;
- framer держит байты между DEC 2026 BSU/ESU, сбрасывает их по ESU, таймеру 50 мс,
  лимиту 4 МиБ или EOF; вынимает из потока OSC 0/2/9/777 и `BEL` в виде сигналов
  (`bell`, `osc_title`, `osc9_notify`, `osc777_notify`, `osc9_4_progress`), остальные OSC
  пропускает; незавершённый OSC отдаётся сырым через 250 мс или 4 КиБ;
- для Cursor `ScreenWatch` сообщает `input_submitted`, `screen_quiet` (тишина 1 c),
  `screen_active` и последние 12 строк (≤2 000 символов) — ядро решает, что это значит;
- не импортирует контракты, `gi`, `mcp`, `msgspec`, SQLAlchemy, uvicorn и watchdog.

---

## 9. Безопасность и устойчивость

Что реализовано в 0.1.1:

- **Изоляция транспорта.** UI и CLI принимаются только из mount namespace ядра; процесс из
  контейнера может быть лишь sidecar с одноразовым токеном (`SIDECAR_TOKEN_INVALID`,
  `ROLE_FORBIDDEN`).
- **Ingest.** Bearer-токен на вкладку, проверка `Origin`, тело ≤ 1 МиБ, ограниченная
  очередь, `problem+json` без утечки внутренних деталей.
- **Git.** Единый runner с перекрытием конфигурации репозитория, `--no-ext-diff`,
  `--no-textconv`, `hooksPath=/dev/null` для фоновых операций, `protocol.ext.allow=never`,
  отключённые подписи и внешние программы; таймауты и лимиты вывода.
- **Согласования.** Nonce, срок жизни, дедупликация, отдельная обработка повторной доставки,
  allowlist с точной формой команды, риск-теги для неразобранных команд.
- **Чекпоинты.** Приватный индекс, запрет `stash`/`reset --hard`, защитный снимок перед
  восстановлением, одноразовый confirm-токен, сохранение `HEAD` и индекса.
- **Пути.** Ссылки вне vault не разрешаются, симлинки наружу не разыменовываются
  (`PATH_NOT_ALLOWED`), PNG пишется приватным файлом в рабочей копии.
- **Процессы.** Orphan scan по маркеру `ACONSOLE_TAB` перед запуском агента; неудачное
  сканирование блокирует запуск; ошибка остановки контейнера не считается успехом.
- **Устойчивость.** Writer-очередь, журнал Git-операций, восстановление намерений после
  сбоя, повторная регистрация токенов, ограниченные кольца и очереди, backoff и resync.
- **Codex.** Доверие только семи собственным command-hooks с индивидуальными хэшами;
  `config.toml` не изменяется; при отказе функции просто не заявляются.
- **UI.** Контраст проверяется отдельным gate (`tools/contrast_check.py`, WCAG AA);
  API floor не даёт использовать GTK/libadwaita/VTE новее целевых версий.

Чего в 0.1.1 нет: проверки монтирования в контейнеры агентов, подтверждения закрытия окна
при работающем агенте, режимов согласований «разрешать/запрещать», полной матрицы
возможностей harness'ов, подписи пакета GPG.

---

## 10. Установка и пакет

### 10.1. `.deb`

- Собирается `.venv-ui/bin/python packaging/build_deb.py` (в CI) или
  `.venv-core/bin/python packaging/build_deb.py` в `dist/`.
- Внутри: два переносимых Python под `/opt/aconsole/{core,ui}` с рантайм-зависимостями
  из `uv.lock`, только отслеживаемые Git файлы `src/aconsole` и `migrations`
  (миграции ставятся как `aconsole/_migrations`), `.desktop` и иконки.
- Зависимости пакета: `git`, `podman`, `gir1.2-gtk-4.0 (>= 4.14)`, `gir1.2-adw-1 (>= 1.5)`,
  `gir1.2-vte-3.91`, `libgirepository-2.0-0`, `libcairo2`, `libcairo-gobject2`, `libffi8`.
- Launch-скрипты `/usr/bin/aconsole` и `/usr/bin/aconsoled` снимают `PYTHONHOME`/`PYTHONPATH`
  и запускают нужный интерпретатор с `PYTHONNOUSERSITE=1`.
- `postinst` компилирует байткод и обновляет кэши desktop/icon; `prerm` удаляет
  `__pycache__`. Пользовательские данные не затрагиваются.
- Пакет собран для `amd64`, не подписан GPG; SHA-256 публикуется в релизе.

### 10.2. Из исходников

`install.sh`:

- синхронизирует `.venv-core` (3.15t) и `.venv-ui` (3.15 + группа `ui`) из `uv.lock`
  с `--locked --no-python-downloads`;
- проверяет, что core действительно без GIL, а UI — с GIL, иначе падает;
- ставит ярлык и иконки в `XDG_DATA_HOME` пользователя;
- не ставит системные пакеты и не повышает привилегии;
- поддерживает `--dry-run` и требует `--allow-outside-home` для checkout вне `$HOME`.

Зависимости Python (`pyproject.toml`): `alembic==1.20.1`, `mcp==2.3.0`, `msgspec==0.22.0`,
`ruamel.yaml==0.19.1`, `sqlalchemy==2.1.6`, `uvicorn==0.54.0`, `watchdog==6.0.0`;
группа `ui`: `pygobject==3.58.1`, `pygobject-stubs==2.20.0`, `rapidfuzz==3.14.6`.

---

## 11. Проверки, тесты и инструменты

### 11.1. Статические проверки

`tools/check.sh` запускает:

1. `ruff` (правила `ALL` с исключениями, длина строки 120);
2. `basedpyright` в режиме `all` с `failOnWarnings`;
3. `import-linter` — шесть правил слоёв;
4. `check_no_js.py` — запрет JavaScript в UI;
5. `check_no_raw_sql.py` — raw SQL только в `persistence/engine.py`;
6. `contrast_check.py` — контраст WCAG AA по style sheet;
7. `check_contracts.py` — перегенерация схем, отсутствие ломающих изменений, соответствие
   контрактов, API floor;
8. `api_floor_check.py` — запрет API новее GTK 4.14 / libadwaita 1.5 / VTE 0.76
   (плюс фикстура, которая обязана падать).

### 11.2. Тесты

| Набор | Тестовых функций |
|---|---|
| `tests/core` | 679 |
| `tests/gui` | 217 |
| `tests/gate` | 129 |
| `tests/hostrun` | 86 |
| `tests/ui` | 79 |
| `tests/contracts` | 8 |
| корневые `tests/test_*.py` | 14 |
| `tests/smoke` | 1 |
| `tests/unit` | 2 |
| **Всего** | **1 215** |

Всего в каталоге `tests` — 217 Python-файлов (с вложенными каталогами и `conftest.py`).

Команды из README и CI:

```sh
tools/check.sh

.venv-core/bin/python -m pytest tests/core tests/contracts tests/gate \
  tests/hostrun tests/smoke tests/test_package.py tests/test_packaging.py tests/test_contrast.py

xvfb-run -a .venv-ui/bin/python -m pytest tests/gui tests/ui tests/smoke
```

Покрытие проверяется объединённо (`fail_under = 90`). В CI `tests/unit` не запускается
в основном прогоне (он содержит пороги времени, измеренные на конкретной машине), а
длительный тест `test_scrolling_a_long_history` исключён явным `--deselect`. При
`AF_UNIX path too long` нужен короткий `--basetemp`.

Документы API и планы лежат во внешнем репозитории, подключаемом как `docs/`
(каталог в `.gitignore`); без него соответствующие сверки пропускаются и сообщают об этом.

### 11.3. Инструменты разработчика

| Инструмент | Назначение |
|---|---|
| `tools/hostrun` | сервер именованных действий поверх Unix-сокета: `check`, `test`, `gate`, `gate-review`, `git`, `ui-run`, `ui-snapshot`, `fake-core`, `core-run`, `podman-ps`, `deps-sync`, `distrobox-dry-run`, `conformance`, `devtools`; `check`/`test`/`gate` выполняются внутри `devtools` |
| `tools/gate` | фазовые ворота: `phase_gate`, `done_when`, `no_placeholders`, `diff_guard`, `protect_paths`, `quick_check`, `shell_guard`, `stop_gate` |
| `tools/codegraph` | экстракторы для Python, TS, Rust, C# |
| `tools/git_bench.py`, `tools/git_snapshot.py` | замеры и синтетические репозитории |
| `tools/mcp_bench.py` | замер MCP |
| `tools/ui_rss.py`, `tools/ui_snapshot.py` | память UI и снимки представлений |
| `tools/gen_state.py`, `tools/regen_stubs.sh` | состояние проектов и заглушки типов |
| `src/aconsole/devcli` | `snapshot` и `events` по сокету, проверка соответствия MCP |

### 11.4. CI

`.github/workflows/check.yml` на `ubuntu-24.04`: ставит GTK 4, libadwaita, VTE,
`xvfb`, `git`, `podman`, `desktop-file-utils`; создаёт `.venv-core` (3.15t) и `.venv-ui`
(3.15+gil); запускает `tools/check.sh`, core-тесты с покрытием, UI-тесты под `xvfb`,
объединяет покрытие, собирает `.deb`, устанавливает его и проверяет запуск `aconsole`,
`aconsoled --help`, а также что core без GIL, а UI с GIL.

### 11.5. Приёмка 0.1.1

| Проверка | Результат |
|---|---|
| `tools/check.sh` | пройден: ruff, basedpyright (0 ошибок), 6 правил слоёв, 10 проверок контрактов, API floor, отсутствие JS/raw SQL, контраст |
| Полный core/contracts/gate/hostrun/smoke/package/contrast | 1660 passed, 1 failed (таймаут сокета в knowledge store), 1 skipped; отказ успешно повторён отдельно |
| Полный GUI/UI/smoke | 391 passed, 1 failed (режим bracketed paste), 1 deselected; отказ успешно повторён отдельно |
| `tests/unit` (10 000 коммитов, 20 000 файлов, 8 worktree) | 2 passed |
| Git benchmark | завершён, см. `artifacts/git-0.1.1/GIT-BENCH.md` |
| Распакованный пакет | core, миграция `0006_session_history`, RPC, рестарт — пройдены |
| Установленный пакет | `0.1.1 install ok installed`, побайтовое совпадение 274 файлов |
| GTK UI с собственным Python | exit 0, снимок `artifacts/git-0.1.1/package-smoke.png` |

Сквозной прогон с четырьмя настоящими CLI остаётся ручной проверкой: в приёмке
подтверждены Codex (распознавание PNG) и частично Cursor; Claude и OpenCode сквозную
проверку не проходили из-за отсутствия окружений. Два сохранённых результата перезапуска
установленного приложения имеют статус `failed` и не выдаются за успех.

---

## 12. Ограничения, известные дефекты и расхождения

### 12.1. Общие ограничения

- Прототип; Linux (X11/Wayland), другие ОС не тестировались.
- AConsole не создаёт контейнеры, не ставит и не логинит CLI, не настраивает монтирование.
  Если в контейнер смонтирован весь домашний каталог, база `~/.local/share/aconsole`,
  `~/.bashrc` или пользовательские systemd-юниты, агент может их изменить.
- Однопользовательский режим, нет серверного развёртывания.
- Scope файлов не блокирует параллельную запись: для параллельной работы нужны отдельные
  worktree; внешние редакторы и процессы пользователя остаются внешними писателями.
- Выборочная отмена вклада агента и автоматический запуск цепочек по DAG не входят в 0.1.1.
- Пакет только `amd64`, без GPG-подписи.

### 12.2. Незакрытые узлы плана на момент 0.1.1

В `plan-1` на 2026-11-07 оставались `PLAN-2` (полный GTK/UI-прогон) и `PERF-2`
(задержка writer tick) красными, а `AUD-2`, `QA-3`, `QA-4`, `QA-5` заблокированы
внешними условиями (живое окно/CLI, CI, лицензия). Проверки на настоящем окне и всех
четырёх CLI — отдельная ручная приёмка. Подробности: [CHANGELOG.md](CHANGELOG.md) и
`artifacts/plan-refresh-2026-11-06/REPORT.md`.

### 12.3. Ограничения по агентам

- OpenCode и Cursor не имеют hooks: inline-согласований и автоматических чекпоинтов у них
  нет, статус определяется по экрану; надёжность этих статусов не подтверждена на всех CLI.
- Cursor и OpenCode отдают лимиты через непубличные endpoint'ы, формат может измениться.
- Поддержка нативных вложений (PNG) подтверждена только в Codex; в Cursor путь остаётся
  текстом; сквозная проверка Claude и OpenCode не проводилась.
- `grok` присутствует в перечислении `AgentKind`, но адаптера и контейнера у него нет.

### 12.4. Что хранится в настройках, но не применяется в 0.1.1

| Поле | Фактическое поведение в 0.1.1 |
|---|---|
| `notify_when` | сохраняется; уведомления о согласованиях приходят при неактивном окне независимо от значения |
| `quota_warn_pct`, `quota_critical_pct` | сохраняются; уровни считаются по фиксированным 80 % и 95 % |
| `codegraph_languages` | сохраняется; индексатор использует фиксированный набор языков |
| `checkpoints_keep` | сохраняется; периодическая уборка применяет его к legacy-чекпоинтам, а список в памяти ядра остаётся с умолчанием 100 |

Редактор настроек меняет только семь полей (шрифт, размер, scrollback, тема, отображение
квот, режим согласований, время удержания); остальные значения можно записать через
`settings/update`.

### 12.5. Известные расхождения и дефекты 0.1.1

- **Палитра.** Маршруты `palette/items|execute|resetRanking` обслуживает только fake core;
  в настоящем ядре они возвращают `METHOD_NOT_FOUND`.
- **`session/list`.** Реализован только в fake core, поэтому выбор существующего нативного
  диалога CLI в UI недоступен, а `agent --resume` для Cursor практически недостижим:
  идентификатор сессии получить неоткуда.
- **Форк.** `tab/fork` в ядре есть и ограничен capability `fork` (Claude, Codex), но в UI
  0.1.1 кнопки для него нет — метод доступен только через типизированный клиент. Отдельно
  в известных дефектах числится поведение форк-копии (её исправление отнесено к 0.1.2).
- **Детали согласований.** Для `Write`/`Edit`/MCP показываются не все поля: путь, diff и
  аргументы инструмента выводятся частично; длинные команды могут обрезаться.
- **Режимы согласований.** Настраиваются только место вопроса (`inline`/`native`) и
  разовые/сессионные решения; режимов «спрашивать/разрешать/запрещать» и уровней
  «глобально/проект» нет.
- **Закрытие окна.** `app/quitCheck` в контракте есть, но UI его не вызывает: окно
  закрывается сразу, ядро останавливается после скрытия окна. Подтверждения при занятых
  вкладках или ожидающих согласованиях нет.
- **Preview и восстановление результата исполнителя.** `WorkSession-history` хранит
  `oid` снимка, но в 0.1.1 и `workSession/integratePreview`, и `workSession/restore`
  работают от изменяемого ref (`snapshot.ref`, `target.ref`), а не от записанного OID:
  если ref сдвинуть после `workSession/result`, примется или восстановится другое дерево.
  Валидация `source_oid`/`target_oid` в `workSession/integrate` при этом есть. Привязка
  preview и restore к записанному OID исправлена после релиза (GIT-1, GIT-2).
- **README и код.** Часть формулировок в README 0.1.1 расходилась с поведением
  (в частности, resume у Cursor и статус палитры): актуальные формулировки — в этом
  документе (§12.5) и в сокращённом README.
- **Заявленные, но не задействованные UI возможности.** `terminal/screenTextReply` в ядре
  отвечает, но запрос текста экрана ядро делает только в нереализованном `/switch`.

---

## 13. Что дальше (0.1.2)

0.1.2 планировалась после закрытия `plan-1`; версия в `pyproject.toml` до этого остаётся
`0.1.1`. Направления (подробно — в [CHANGELOG.md](CHANGELOG.md)):

- безопасность hostrun, review gate и self-approval, доступ агента к базе AConsole и
  домашнему каталогу хоста, оставшаяся гонка динамических Git-драйверов;
- режимы согласований и честная матрица применимости по harness'ам, детали Write/Edit/MCP,
  длинные команды;
- форк и выбор нативной сессии, надёжные статусы OpenCode/Cursor, устойчивость sidecar;
- подтверждение закрытия окна и вкладки, палитра на настоящем ядре, режимы новой вкладки,
  локализация и различимость состояний;
- разгрузка блокировок из общего потока и event loop, lease MCP между планами,
  устойчивость IPC и очереди ingest;
- финальные QA-проверки, CI/сборка/контракты, лицензия (в 0.1.1 её не было).

---

## 14. Ссылки

- Репозиторий: <https://github.com/secachiss-code/agent_console_p>
- Релиз 0.1.1: <https://github.com/secachiss-code/agent_console_p/releases/tag/v0.1.1>
- Релиз 0.1.0: <https://github.com/secachiss-code/agent_console_p/releases/tag/v0.1.0>
- CI: <https://github.com/secachiss-code/agent_console_p/actions/workflows/check.yml>
- Issues: <https://github.com/secachiss-code/agent_console_p/issues>
