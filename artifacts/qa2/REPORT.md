# QA-2 — дополнительные исправления безопасности

База: `ddb1024fd31711aaea970461e03af235d89238c2`, узел plan-1/node-13.
Проверены SEC-3 (`1795dd49`), SEC-4 (`d2ea3aad`), SYS-4 (`ae646259`),
SEC-5 (`6edcf496`) и последующее исправление Git hostrun SEC-12 (`37728b85`).

## Результат

**457 passed, 1 skipped**, 45.86 с. Прогон текущего worktree внутри devtools,
PYTHONPATH его src, Python 3.15 из .venv-core. Команда: python -m pytest
-o addopts= -q с путями из verification.json.
Пропущен только test_client_lists_match_known_actions: связанный docs/plan
отсутствует. Пример policy, доступные actions и ограничения аргументов
проверяются отдельно и проходят. Производственный код не менялся.

## Дефект → исполняемая проверка → результат

| Область | Проверки и результат |
| --- | --- |
| SEC-3: core-run/deps-sync/ui-run/ui-snapshot/fake-core | test_remaining_checkout_actions_only_execute_podman и remaining_actions_dispatch_only_container_commands проверяют argv и HTTP dispatch всех вариантов; argument_validation отклоняет injection/path traversal. Host-команды — только Podman. PASS. |
| SEC-3: fake-core lifecycle и PID namespace | container_fake_entry_uses_container_lifecycle проверяет явный внутренний CLI; container_fake_lifecycle_starts_and_stops_a_real_process запускает временный Python с Unix-сокетом, запрещает дублирующий start, останавливает его и удаляет PID-файл в том же runner. PASS. |
| SEC-4: self-approval | approve_is_denied_even_when_local_policy_grants_review проверяет четыре агента по HTTP с расширенной policy; old_approval_plan_cannot_bypass_dispatch_validation отклоняет старый plan в runtime. PASS. |
| SEC-4: поддельный gate/review и replay | forged_protected_approval_cannot_promote_a_failed_gate, replayed_approval_never_promotes_later_reports, approval_cannot_be_reused_from_a_copied_repository, malformed/stale/duplicate-key cases. Изменённые отчёты не превращают FAIL в PASS. PASS. |
| SEC-4: изменяемый runner возвращает 0 | zero_exit_and_forged_success_are_unverified покрывает task/audit и поддельный JSON PASS. Итог всегда exit 1/UNVERIFIED; исходный отчёт не переписывается. inspection_does_not_create_an_approval сохраняет положительный сценарий просмотра. PASS. |
| SYS-4: stop failure, повторный scan и namespace PID | test_tabs_launch_stays_down_when_a_stop_or_a_survivor_remains проверяет ошибку первого kill, surviving PID, второй scan и последующий успешный запуск. stop_in_container_reports_a_podman_failure не подавляет ошибку Podman; orphans_are_stopped_inside_their_container_not_on_the_host не допускает host os.kill контейнерного PID. PASS. |
| SYS-4: соседние lifecycle пути | test_round6_k25_restart_stops_the_old_process_and_its_orphans и tabs_restore проверяют restart/sidecar disconnect. test_codex_watch_recovery дополнительно подтверждает kernel peer PID через реальный Unix-сокет. PASS. |
| SEC-5: pending/ALLOW/DENY другого таба | other_tab_cannot_share_pending_or_settled_approval покрывает три фазы со stable event id и без него; event_id_cannot_replay_a_different_context меняет session/tool/input. Новый контекст остаётся pending. PASS. |
| SEC-5: close cleanup | dropping_one_tab_keeps_other_pending_and_replay_identity, close_forgets_settled_decisions_and_rules_only_for_that_tab и public_tab_close_releases_only_its_held_hook. Через настоящий Daemon/Unix IPC/HTTP первый hook освобождается в native без ALLOW, второй остаётся pending и отдельно получает DENY. PASS. |
| SEC-12: оставшийся Git bypass | git_config_programs_are_confined_to_inner_command запускает настоящие clean-filter/fsmonitor только во внутреннем Git в devtools; HTTP покрывает пять Git-форм и отсутствие host fallback. review_git_dispatches_only_podman сохраняет этот запрет для inspect. PASS. |

## Обзор исходников

Проверены build_plan и HTTP policy/container/cwd validation, internal
container-fake dispatch, argv/environment и runtime review/gate ветки.
Клиент передаёт named action и проверенные args, а не произвольный Plan.
Оставшиеся checkout interpreters/scripts находятся в argv podman exec.
При отказе контейнера нет возврата к прямому выполнению checkout на host.
Действия podman-ps/distrobox-dry-run сохраняют фиксированные ограниченные
аргументы. Shell interpolation из запроса не добавлено.

Review-файлы, task bases и код gate недоверенны: ни digest, ни JSON PASS,
ни нулевой exit изменяемого runner не дают доверенный PASS. Отдельный
runtime guard не позволяет обойти отказ approve старым review Plan.
Inspect возвращает diff, не решение о приёмке.

В ApprovalBook input key включает tab/session/tool/input digest; event key
добавляет event_id к полному input key. Bootstrap вызывает drop_tab
на TabRemoved; broker освобождает только соответствующие waiters/views/
timers и забывает удалённые settled решения. Существующие nonce/expiry/
one-time/replay/native-fallback сценарии проходят вместе с новыми.

## Оставшиеся границы

В проверенных маршрутах исходников дополнительных обходов сверх
устранённого SEC-12 не обнаружено. Это не утверждение о полной изоляции
установленной системы: политика и уже загруженный сервис hostrun не
обновлялись, его код на диске не защищён этой проверкой от writable mounts.
SEC-7 остаётся отдельным требованием реальной изоляции HOME/runtime/кода
сервиса от агентов. Передача desktop environment для UI-действий сохранена;
изоляция настоящего дисплея этим набором не проверялась.

Доверенный положительный PASS по-прежнему отключён до независимого
verifier GATE-1. SEC-11 относится к GitRunner самого продукта и остаётся
отдельным незавершённым узлом. QA-2 не снимает эти ограничения и не
принимает реальную конфигурацию контейнеров заодно с кодом маршрутизации.

Новые production-правки и новые дефекты в этом узле отсутствуют. Сохранены
отчёт и хэши проверенных модулей; git diff --check PASS. Пользовательские
агенты и контейнеры не запускались/не останавливались; временные процессы
созданы и убраны только тестами. UI/layout не менялись.
