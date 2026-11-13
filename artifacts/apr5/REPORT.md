# APR-5 — Claude Code approval policy

База: 88d5f876 (исправление пользовательского сообщения о сохранении — APR-10).

Политика проекта/глобальная политика теперь участвует в PreToolUse и PermissionRequest
Claude. Автоматическое решение не зависит от наличия UI или выбора inline/native.
Опасные и неизвестные вызовы в allow+dangerous=ask требуют решения; allow+allow
пропускает их. Политика deny имеет приоритет над ранее завершённым allow.
Автоматические решения попадают в audit summary с причиной policy=allow/deny и в
журнал с режимом и подрежимом. Stable delivery replay не дублирует audit.

При запуске настоящий container CLI проверяется через claude --version. Автоматический
режим не запускает незащищённый CLI при версии <2.1.295, отсутствии порта/HOME или
ошибке записи настроек. Проверенный launcher добавляет capability approval_policy;
настройки показывают применение и границы защиты. Permission hooks используют
onFailure=block. Новый запуск в deny также получает permissions.deny=["*"] и dontAsk.
Изменение из deny в другой режим требует новой сессии для снятия статического запрета.

Реальный CLI: **Claude Code2.1.318**. live_probe.py использует настоящий binary, production
launcher и production HTTP decision handler, локальный детерминированный Messages API,
временный HOME и одноразовые marker-файлы. Учётная запись и внешняя модель не используются.
live/results.json и stdout/stderr сохраняют восемь сценариев: allow/plain executes;
allow+ask/risky blocked; allow+allow/risky executes; deny blocked; deny с HTTP503 blocked;
ask/scenario PermissionRequest без автоматического выполнения; allow с HTTP503 blocked.
Это проверка permission engine в -p, а не ручная приёмка интерактивной TUI.

209 focused core/UI-logic checks PASS (complete.xml): доменная дедупликация, HTTP,
настоящие TCP disconnect/daemon IPC, проектный override, UI-loss/native, старые версии
и ошибки launcher. Ruff и strict basedpyright для изменённого production-кода PASS.
Первый sandbox-прогон baseline.xml завершился PermissionError создания TCP socket;
baseline-network.xml сохраняет успешный прогон после разрешения локальных сокетов.

Правила Edit для .claude/.git/~/.claude/~/.aconsole дополняют hooks. Они **не** являются
файловой изоляцией: разрешённый Bash может переписать настройки. Это явно указано
в UI и соответствует принятому TD-SEC7. APR-5 не объявляет погашение долга или
успешную независимую security-приёмку. Для QA-6 критерий защиты файлов остаётся
предметом фактической проверки, а не следствием статуса SEC-7 done.

Протокол сверялся с первичными источниками:
https://code.claude.com/docs/en/hooks и https://code.claude.com/docs/en/permissions.
Приёмка остальных CLI выполняется отдельными узлами. Установленное приложение
этим коммитом не обновляется и не перезапускается.

UI: ui/light.png, ui/dark.png и ui/narrow.png просмотрены: пояснение Claude полностью читаемо, не обрезано по ширине, тёмная тема без светлых пятен. Preview использует явную capability-фикстуру; первые снимки ui-before-fixture сохраняют нативный fake-core без этой capability, это не production failure.
