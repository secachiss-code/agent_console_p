# QA-1 — проверка исходных исправлений

Проверка начата на `5a96265e`; подтверждённый обход исправлен отдельным
SEC-12, коммит `37728b85`. Узел plan-1/node-8.

## Результат

В границах QA-1 критерии подтверждены исполняемыми регрессиями и обзором.
Первоначальные 490 passed/1 skipped не были достаточным доказательством:
дополнительная проба выявила выполнение filter.clean через hostrun git diff.
Находка записана как SEC-12 и устранена до завершения QA-1.
Отчёт SEC-12: ../sec12/REPORT.md; проба исполнялась только внутри devtools.

## Соответствие исходным дефектам

| Узел | Подтверждение |
| --- | --- |
| SEC-1 | test_check_test_and_gate_keep_checkout_code_inside_devtools и HTTP check-тесты фиксируют host argv только Podman. Настоящий external diff/textconv driver не запускается. SEC-12 дополнительно переносит Git и review Git в devtools: clean-filter и fsmonitor больше не являются программами hostrun на хосте. |
| SEC-2 | domain/test_approvals: one_time_allow_does_not_cover_the_next_identical_call, same_event_id_is_one_approval_after_a_one_time_allow, pending_dedup_shares_one_request_and_remembers_the_second_event. ApprovalHold проверяет отмену одного waiter's future, идемпотентность решения, реальные permission-hook ответы и Daemon/IPC/audit. |
| SYS-1 | test_tabs_launch_stays_down_when_the_orphan_scan_fails и stop_or_a_survivor_remains: ошибки сканирования/остановки и оставшийся процесс блокируют launchSpec. Успешный сценарий и пустой scan сохраняются. |
| SYS-2 | test_worktrees_keep_the_tree_when_remove_is_refused_after_a_clean_check и clean_tree_when_git_refuses_without_a_new_change сохраняют реальный worktree. Актуальное закрытие вкладки теперь сохраняет WorkSession: test_tabs_close_keeps_every_workspace_registered, test_a_task_survives_tabs_and_restart. cleanupWorker отказывает при dirty/stale result, а late nonignored write сохраняется обоими Git-адаптерами. |
| SYS-3 | GUI CoreClient held_lock_waits_for_the_socket_and_does_not_spawn, held_lock_reports_when_the_socket_never_appears, free_lock_still_starts_one_core; настоящие процессы в test_core_recovery подтверждают рестарт и удержание lock до остановки worker/writer. |
| MEM-1 | VTE restart_on_one_terminal_keeps_a_single_exit_handler повторяет запуски одного виджета. ApprovalBook/ApprovalHold удаляют истёкшие решения и закрытые табы. VaultWriter удаляет echo после TTL; конкурентная регрессия CORE-8 также проходит. test_session_cost проверяет ограничение числа OpenCode sessions и сохранение суммы после удаления; test_ipc_peer_history_keeps_the_newest_entries ограничивает диагностику. |

Поведение SYS-2 оценено с учётом WorkSession из версии 0.1.1: закрытие таба
само больше не удаляет workspace. Проверены и сохраняемая сессия после
рестарта, и отдельные пути cleanup. Старое ожидание немедленного удаления
чистого worktree не возвращалось ради исходного сценария.

## Выполненные наборы

Основной прогон внутри devtools, core Python 3.15, PYTHONPATH текущего checkout:

```
tests/hostrun
tests/core/domain/test_approvals.py
tests/core/test_approval_hold.py
tests/core/test_approval_window.py
tests/core/test_approval_tab_isolation.py
tests/core/test_ingest_http.py
tests/core/test_tabs.py
tests/core/test_tabs_restore.py
tests/core/test_worktrees.py
tests/core/test_opencode_cursor.py
tests/core/test_vault_writer.py
tests/core/test_ipc_a.py
tests/gui/test_core_client.py
```

**515 passed, 1 skipped**, 44.73 с. Skip: test_client_lists_match_known_actions,
поскольку связанный docs/plan отсутствует в checkout. Пример policy и
allowlist проверены другими тестами.

После уточнения тестового адаптера повторены test_server.py,
test_gate_reviews.py и test_session_cost.py: **168 passed**, 24.38 с.
Часть этих тестов повторяет основной прогон; числа не суммируются.

Дополнительно tests/core/test_work_sessions.py, test_cleanup_discard_race.py,
test_core_recovery.py и test_sidecar_resilience.py: **35 passed**, 21.23 с.
Это включает настоящие временные Git-репозитории, core-процессы и PTY child,
а не только fake ports.

На хосте с .venv-ui, без окна и пользовательского агента:
tests/gui/front/test_b5a_terminal.py — **13 passed**, 0.46 с. В тестах
использован fake terminal через настоящий GTK/VTE adapter; существует
одна старая gi.events DeprecationWarning для Python 3.16. Это не
интерактивная проверка клавиш и живого терминала из AUD-2.

Изменённые строки hostrun: 0 Ruff diagnostics при принудительном включении
обычно исключённых legacy-файлов; на остальных строках остаются 43
диагностики. Полный зелёный lint этих файлов не заявляется. diff-check PASS.

## Обзор границы безопасности

Проверены allowlist build_plan, argv/окружение execute_argv, HTTP
policy/container/cwd-валидация и пути check/test/gate/Git. Имя действия
не становится shell-командой. Для checkout-кода host argv — Podman;
Git-драйверы и произвольные тесты остаются внутренними программами devtools.
При отказе запуска контейнера исполнение прекращается.

Результат ограничен исходниками и воспроизводимыми тестами. Пользовательский
сервис hostrun не перезапускался: его уже загруженный код не обновляется
автоматически коммитом. Ни независимость mount namespaces, ни отсутствие
доступа из devtools к HOME хоста этим обзором не доказаны — SEC-7 остаётся
blocked. Требования QA-2, SEC-11 и GATE-1 не принимаются заодно с QA-1.
Подтверждение из отчёта SEC-1, что --no-textconv достаточно против всех
Git-драйверов, заменено находкой и исправлением SEC-12.

Установленное ядро, реальные контейнеры, политика пользователя и его
рабочие данные не менялись. UI/layout не менялись; снимки не требуются.
