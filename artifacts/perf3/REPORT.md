# PERF-4: проверка границ fault injection для PERF-3

2026-11-06, общий main, база 82b49fd. Диагностический узел node-134.

В исходном shutdown-тесте writer._WAIT_S=0.03 устанавливается до daemon.serve.
Этот параметр используется также для deadline queued Writer.call. В полном
сборе тестов восстановление планов не успевает начать такую работу за 30 мс:
serve падает до создания сокета и до проверяемого shutdown. Traceback сохранён.

Кандидат переносит ту же инъекцию после _connect. Значения 0.03, 0.15 и 2 секунды,
pytest.raises(TimeoutError), проверки lock и cleanup сохраняются. AST всех
исходных assert сравнен с AST диагностической копии: совпадает.

| Исполнение через hostrun | Результат |
| --- | --- |
| Диагностический кандидат отдельно, три процесса | PASS, PASS, PASS |
| Исходный тест с collection только recovery-файла | PASS |
| Исходный тест + tick, collection полного core-набора | recovery FAIL, tick PASS |
| Оригинал + кандидат + tick при общем collection | оригинал FAIL, кандидат PASS, tick PASS |

Последний запуск подтверждает различие в одном процессе при общей сборке тестов.
Изолированный PASS оригинала показывает зависимость от условий запуска;
исходный тест не объявляется безусловно падающим. Кандидат прошёл всего четыре
исполнения, три отдельных и одно совместное. Это не полный core после правки.

Все команды: hostrun test core; полные argv и exit в hostrun-audit.json.
candidate1/2/3.txt, baseline.txt, baseline_pair.txt и mixed.txt содержат вывод.
Только пробелы в концах строк вывода удалены. Изоляция HOME/XDG/Git перенесена
из tests/conftest.py и tests/core/conftest.py для отдельного запуска артефакта.
Диагностический daemon использует существующие fake podman-порты, не настоящий CLI.

Исходный tests/core/test_core_recovery.py не изменён: SHA-256 в source.sha256.
Зарезервированные файлы не менялись. CORE-7 и PERF-3 по-прежнему блокируются
UX-7 и SEC-6. Разрешение пользователя на применение конкретного кандидата
запрошено отдельно; без него reserved исходник не меняется.

candidate.diff — patch с нулевым контекстом. Проверка применимости:
git apply --unidiff-zero --check artifacts/perf3/candidate.diff
Это только предложение, не применённое исправление.

Диагностические исходники архивированы как conftest.py.txt и
test_recovery_setup.py.txt, чтобы не включать эксперимент в штатную коллекцию.
Для повторения восстановить имена .py в этой директории и исполнить:
hostrun test core artifacts/perf3/test_recovery_setup.py
Совместный запуск:
hostrun test core -k 'persistence_b_tick or core_keeps_its_lock_until_a_slow_writer_has_stopped' tests/core tests/contracts tests/gate tests/hostrun tests/smoke artifacts/perf3/test_recovery_setup.py

После разрешения: применить точечный patch, повторить recovery/writer проверки,
затем полный hostrun test core и hostrun check. Только их успешный результат
может подтверждать исправление PERF-3. PERF-2 остаётся отдельным наблюдением.
