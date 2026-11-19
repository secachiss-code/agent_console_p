# GAT-1 — предпроверка патча до публикации

Узел node-916, plan-900, fence2, attempt2, реализация `9650cb72`.
Завершён штатным `node_update`: verifying → done, без human override.
Доска: 12 done, 11 pending, verifying/blocked нет; GAT-2 ready.

MCP step_submit принимает необязательный patch, максимум 256 КиБ UTF-8.
Без patch сохранён прежний путь. Git разбирает unified diff в отдельном
каталоге; все пути сверяются с frozen scope до копирования. Бинарные diff,
rename/copy и смена специальных file modes отклоняются. Симлинки,
каталоги и hardlinks не становятся источником или целью публикации.
Копируются только затронутые regular files; git apply меняет только копии.
Frozen format/fix/lint/types проверяют каталог копий. Перед публикацией
повторно проверяются lease, scope и исходные байты каждого файла.
После публикации сохраняется обычный L2: Micro с тестами на worktree
выполняется на verify; откат теста остаётся у step_reset.

Podman exec не умеет добавлять mounts. Поэтому preflight использует
одноразовый podman run --rm --pull=never из image ID выбранного running
контейнера. Один bind — каталог копий в /tmp/aconsole-preflight; сеть
отключена, rootfs read-only, capabilities сброшены, env очищен перед
инструментом. Дом, socket ядра, БД и worktree не подключаются. Обычные gates
продолжают использовать exec. Источник mount обязан находиться в приватном
корне preflight; .., выход наружу и symlink отклоняются. Нет доступного
контейнера/инструмента — GATE_RUNNER_UNAVAILABLE, без host fallback.

Scope GAT-0/GAT-1 расширен production composition infrastructure/pipeline.py
и IPC capability negotiation session.py через public native plan/save с
ожидаемыми contract hashes и audit note. Запрос/ответ — repair-*.json.
Попытка1 abandoned штатным ремонтом, до исполнения gates; попытка2
выполнена полностью. Прямых записей в БД не было; audit.py читает mode=ro.
Возможность preflight.v1 согласуется IPC handshake; API_VERSION не менялся.

## Проверки

Локально:
`.venv-core/bin/python -m pytest tests/core/test_mcp_pipeline.py tests/core/test_gate_runner.py -q`
— 41 passed, 1 skipped (опциональный отдельный live exec timeout).
Проверены scope/traversal, отсутствие записи при отказе линтера/контейнера,
симлинки/hardlinks, UTF-8 byte limit, mount-источник, MCP schema/transport,
capability negotiation и прежняя цепочка без patch. Ruff format/check и
basedpyright по всем девяти изменённым файлам чистые. Старые тесты также
исправлены для строгой типизации: явные unused results, словари object и
typed JSON. Узкое reportPrivateUsage=false оставлено только в двух
white-box тестовых модулях, использующих внутренние helpers/deadlines;
проверки типов production-кода не ослаблены.

Реальный host Podman probe прочитал isolated-preflight из scratch-only
mount: live-preflight.json и воспроизводимый live_preflight.py. Probe
проверяет запуск/изоляцию mount, а не наличие Ruff/Pyright в devtools image.
Для боевого preflight образ должен содержать frozen argv/toolchain без
host mounts; подготовка настоящего devtools digest остаётся условием L0.
Текущий образ Debian не объявлен готовым образом инструментов.

Micro attempt2: 5 passed, 0 failed; тестовая команда 44 passed, 1 skipped.
Receipt: cd86604cc8d6ad417fa47b06a4f99dbe61c82b0f25a70130bc6896ea8830d77e.
Blast attempt2: 2 passed, 0 failed; 41 passed/1 skipped в команде узла,
413 passed/2 skipped в зависимых тестах.
Receipt: fd68e77c0997838488eb418d7b0934e76b431d2e067f056c7e441b83498765dd.
Обе квитанции относятся к одному state_hash:
9e147052d705c62ba459ab88336efb2404e7128963ea7c1820e320ff71e52608.
Blast превысил транспортные 60 секунд; успех подтверждён durable receipt
и passed-попыткой, повторного исполнения не было.

## Остаток

GAT-2: расход попытки, verification_gate_cache для предпроверки и итоговая
однострочная диагностика. GAT-1 возвращает refusal без продвижения шага,
но ещё не расходует попытку и не кэширует этот отказ. Сравнение байтов перед
публикацией не является файловой транзакцией с другими процессами.
Следом FTS5 и MEM; L0 не закрыты, версия и основная установка не менялись.
