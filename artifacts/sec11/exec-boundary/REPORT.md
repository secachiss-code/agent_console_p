# SEC-11: граница exec и отказ от textconv для show/log

База: `411ef4f2be115fe99f8ab618c7ca13aa47301f9f`.
**Общая приёмка SEC-11: FAIL.** Гонка добавления конфигурации остаётся в продукте.
Изменение чтения истории ниже — отдельное проверенное усиление, не устранение этой гонки.

## Продуктовое изменение

GitRunner теперь передаёт `--no-ext-diff --no-textconv` также командам `show` и `log`,
как уже делал для `diff`. Результат строит сам Git, без внешнего textconv-процесса.
Прежние overrides neutral driver сохраняются для других путей; hooks не изменены.

Две новые обычные регрессионные проверки подменяют нейтральную helper-программу
маркером. До изменения обе падают: даже безопасная подмена репозиторного textconv
вызывает отдельную программу. После изменения marker не появляется, а исходное
содержимое файла остаётся в patch output. Старые проверки не ослаблены.
Смысл этой проверки — не утверждать, что доверенный /usr/bin/cat вредоносен,
а подтвердить отсутствие необходимости запускать вообще какой-либо textconv helper.

## Эксперимент, который нельзя включить как готовое исправление

`guard.py.txt` в отдельном дочернем процессе ставит no_new_privs и seccomp BPF:
позволяет начальный execveat по открытому CLOEXEC descriptor Git, возвращает EPERM
для execve и иных execveat, блокирует ptrace и x32 syscall numbers, проверяет ABI.
`guard_plugin.py` подменяет GitRunner._execute только внутри отдельного pytest-прогона.
Продукт этот код не импортирует и не использует; отдельного feature flag нет.
Никаких системных профилей, sysctl, пакетов или настроек пользователя не менялось.

Это лабораторный ограничитель для доверенного Git, а не проверенный sandbox для
произвольного машинного кода. Оговорки первоначального fd, его возможного повторного
использования и других каналов исполнения требуют отдельного анализа перед продуктом.
Проверена только x86_64; aarch64-константы не являются результатом ARM-проверки.
Linux отдельно подчёркивает, что seccomp сам по себе не является sandbox:
[официальная документация ядра](https://www.kernel.org/doc/html/latest/userspace-api/seccomp_filter.html).

Исходный race_probe.py сохранён byte-for-byte. Без guard он по-прежнему FAIL:
filter.late.clean, добавленный после перечисления имён, запускает marker.
С guard этот же сценарий PASS, однако стандартный pre-commit hook не может
выполниться: commit отказывает с Operation not permitted. Это несовместимость с
требованием сохранять осознанно разрешённые hooks, а не успешная общая приёмка.

Первый guard-прогон:25 passed,3 failed. Кроме hook ломались show/log, пытавшиеся
исполнить нейтральный cat. После продуктового изменения:29 passed,1 failed;
остаётся ровно hook. Два новых теста включены в последний набор (всего30).
Отказы guard сохранены, не заменены skip/xfail или проверкой противоположного результата.

## Проверки и артефакты

| Проверка | Результат |
| --- | --- |
| Исходный race до изменения | 1 failed (baseline.xml) |
| Новые patch-read проверки до изменения | 2 failed (patch-reads-before.xml) |
| Product host-exec/environment после изменения | 42 passed (product-focused.xml) |
| Связанные реальные Git/IPC, host | 75 passed (product-related.xml) |
| Эксперимент guard до изменения | 25 passed,3 failed (guarded.xml) |
| Эксперимент guard после изменения | 29 passed,1 failed (guarded-after.xml) |
| Исходный product race после изменения | 1 failed (product-race-after.xml) |

Ruff и strict basedpyright изменённого runner проверены. Первый контейнерный
basedpyright не нашёл configured UI venv, поэтому его exit3 с0errors не засчитан;
проверка повторена на хосте с корректным venvpath.
Новое полное core после этой правки не заявляется: прошлые1517PASS относятся к411ef4f2.

Значения и длительности JUnit, база и SHA-256 — в results.json. Все опасные сценарии
выполнялись только в новых временных репозиториях; приватные данные не использовались.
Контейнерное воспроизведение race подтверждает проблему пути GitRunner, но не является
новым доказательством исполнения именно в host namespace. Предыдущий host-отчёт
SEC-11 сохраняет своё отдельное основание и ограничения.

## Воспроизведение

Из корня worktree с существующим core environment:

```sh
PYTHONPATH=src /home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python -m pytest -q artifacts/sec11/race_probe.py
PYTHONPATH=src:artifacts/sec11/exec-boundary /home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python -m pytest -q -p guard_plugin artifacts/sec11/race_probe.py tests/core/test_git_host_exec.py
distrobox-host-exec /home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python -m pytest -q tests/core/test_git_host_exec.py tests/core/test_git_security_environment.py tests/core/test_git_history.py tests/core/test_git_operations.py tests/core/test_git_merge.py tests/core/test_git_wire.py tests/core/test_checkpoints_git.py
```

Первые две команды должны вернуть exit1 по разным причинам, указанным выше.
Третья — exit0. Для host exec нужно сохранить cwd этого worktree.
Следующий шаг SEC-11 — полноценная изоляция Git с явным сохранением hooks либо
отдельная согласованная архитектура выполнения hooks. Новый проход config и
обнаружение изменений после исполнения не считаются решением.
