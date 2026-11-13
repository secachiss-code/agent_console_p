# APR-7 — Codex permission modes

База: 0bce6ac3. Реальный CLI: **codex-cli0.169.0**. Запуски используют временные
HOME/CODEX_HOME, локальный Responses API и одноразовые marker-файлы. Нет внешней
модели, пользовательских credentials или self-approval в рабочем приложении.

## Реально проверенная семантика

- PreToolUse deny блокирует; allow допускает дальнейшую проверку разрешений.
- PreToolUse ask в этой версии не блокирует вызов (live/never-pre-ask.json).
- При approval_policy=never PermissionRequest не вызывается, escalation отклоняется.
- Через интерактивный app-server on-request → PermissionRequest → allow действительно
  выполняет escalation. Через never тот же вызов не выполняется. Raw JSON-RPC,
  hook payload и доверенные hashes сохранены в final/.
- Устаревший untrusted отвергается самим CLI. Первоначальный диагностический отказ
  сохранён в verified/app-on-request-listing.stderr; применяется on-request.

Production-путь policy/ использует настоящий CodexLauncher и handle_permission:
allow-all создаёт marker; deny не выполняет; allow с нетегированным touch выполняет;
allow+dangerous=ask с rm и без панели блокирует. Рискованный вызов не получает
неподдерживаемый PreToolUse ask.

## Исправления

Новые сессии явно получают approval_policy и sandbox_mode. Ask/allow+ask используют
on-request + workspace-write; deny — never + read-only; только явно выбранный
allow+allow использует never + danger-full-access. Без проверенных hooks, HOME или
порта автоматический режим не запускает обычный незащищённый Codex.

В inline панели PreToolUse удерживается до allow/deny. Timeout, потеря UI, закрытие
таба и «вернуть в native» не дают выполнить неподтверждённый Codex PreToolUse:
такой native-переход протокол не поддерживает, поэтому запрос завершается отказом.
Escalation имеет отдельную область согласования и privilege tag; разрешение PreToolUse
не одобряет автоматически последующий PermissionRequest того же tool_use_id.

После восстановления демона незаверенный ALLOW от hooks запрещён; сохранённый DENY
не забывается из-за пустого кэша проверенных клиентов. Требуется новая сессия для
проверки launcher. UI сообщает границы применения.

## Отказы hook — красный результат сохранён

Старая обёртка с curl без --fail и ||true пропускала инструмент при HTTP503:
live/offline-pre-deny.json. Одного exit2 без stderr оказалось недостаточно:
verified/offline.json также сохраняет выполнение. Это обнаружено настоящим CLI,
а не выведено из документации.

Исправленная обёртка использует curl --fail, exit2 с **непустой причиной** и внешнюю
команду, превращающую падение/отсутствие скрипта в явный блокирующий отказ. PreToolUse
timeout600s, curl590s и hold≤540s оставляют время для ответа отказом.
corrected/offline.json не выполняет инструмент. final/crash.json и final/missing.json
также блокируют; missing останавливает сессию до запроса модели.

## Проверки и границы

184 tests PASS (final-tests.xml): launcher/trust, HTTP, IPC, доменная дедупликация,
timeout/release/UI-loss, native fallback, escalation scope, восстановление и UI logic.
Ruff и strict basedpyright изменённых production-модулей PASS. Исторические raw
CLI fixtures не переписаны: тесты адаптируют только pathname/новое определение hook;
реальные новые определения и hashes отдельно захвачены из0.169.0.

Три UI PNG просмотрены: текст Codex полностью читаем в light/dark/700px, локализация
работает, горизонтальной обрезки и светлых пятен в dark нет. Снимки с явными
capability-фикстурами показывают прокручиваемую часть настроек, не manual CLI приёмку.

Это **частичное покрытие harness**: hosted tools не идут через локальные tool hooks.
В native режиме ask следует on-request Codex; allow+ask без панели блокирует опасные
вызовы. Hooks не защищают изменяемые файлы настроек; TD-SEC7 не погашен. Независимый
security review не объявляется пройденным. Установленное приложение не обновлялось.

Сверка с первичными источниками: https://developers.openai.com/codex/hooks/ и
https://developers.openai.com/codex/config-reference/. Наблюдаемое поведение установленного
CLI имеет приоритет над устаревшими перечислениями значений в документации.
