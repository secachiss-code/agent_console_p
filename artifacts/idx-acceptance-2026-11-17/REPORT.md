# Приёмка IDX-2, IDX-3, IDX-5

Проверки и решения выполнены 2026-11-17 по поручению пользователя
«Приёмку выполни»; итоговое состояние повторно подтверждено 2026-11-18.

| Узел | Итог |
|---|---|
| IDX-2 / node-903 | **done**, attempt 2: Micro-Gate и Blast-Gate passed; принято через human IPC после перехода в verifying |
| IDX-5 / node-906 | **done**, attempt 2: Micro-Gate и Blast-Gate passed; принято через human IPC после перехода в verifying |
| IDX-3 / node-904 | **blocked**: функциональные проверки зелёные, pipeline останавливается на ошибке frozen-scope |

Доска plan-900: **4 done, 18 pending, 1 blocked**, 23 узла;
in_progress/verifying отсутствуют. Приняты также прежние IDX-1 и VLT-3.
L0 не закрывались. Это приёмка реализованных узлов, а не выпуск всей 0.1.4.
Снимок — `plan-900.json`; durable квитанции и журналы — `receipts.json`
и `node-*.txt`. Старые отрицательные квитанции сохранены в истории.

## IDX-2

Реализация — 41d8fca9 поверх подготовки протокола d78eb4c9.
Старая успешная попытка существовала, но её Blast-квитанция не принималась
для текущего набора зависимых тестов. Выполнена свежая попытка 2.

Точная база проверки — 41d8fca9, без более поздних изменений других узлов.
Уточнён docstring file_hashes; эта правка затем перенесена в текущую ветку
коммитом 8254a48f (исходный коммит проверки 8b98e403).
Полный снимок проверяемого дерева хранится в refs/aconsole/attempts/node-903.

Micro: 5 команд passed, receipt
47c85804cbc2fe7466f8776231a5ee48e87d6d4e16a6952242eced083d6afe64.
Blast: 2 команды passed, receipt
b140556503e6b927f41d742e5193a39013d68622117a89ec489a7eb5b92cf51d.
Blast запускает test_cmd узла и 30 зависимых файлов, выбранных ядром.
Проверены сохранение фактов, отсутствие перезаписи рёбер при стабильном
интерфейсе, смена интерфейса и миграция 0011 → 0012 → 0011 → 0012.
MCP-вызов Blast превысил 60 секунд, но контейнерный процесс продолжил работу;
принятие выполнено только после появления успешной durable квитанции.

## IDX-5

Реализация — b3bb4b16; исправленный интеграционный тест hostrun — 182a1f97.
Попытка 2 выполнена на 182a1f97 с уточнённым docstring адаптера
(зафиксирован в 9041c90b). Формат, исправления Ruff, lint, строгая типизация
и выбранные тесты прошли Micro-Gate.

Micro receipt:
123ee1912d851ff2664ba31c58972f42948cad8582330a88d7eb8cf4cd58d477.
Blast receipt:
2cf8695191c33e5f78e61fbf9d69af963ff097a6a05c0d9b210e449e65ca920b.
Blast включает test_cmd узла и зависимые тесты.

Работающий aconsole-hostrun был загружен до изменений argv и всё ещё пытался
вызвать npx. После штатного systemctl --user restart aconsole-hostrun.service
live-запуск через hostrun выдаёт protocol v1, 3 узла и 2 imports-ребра
(`ts-live.json`). Тест реального dependency-cruiser 18.7.0 проверяет dynamic
import, tsconfig alias и отказ исполнять конфигурацию проекта; отдельные
тесты проверяют отсутствующий инструмент, другую версию и отказ CLI.

## IDX-3: проверено, но не принято

Бинарь aconsole-rsgraph установлен в devtools через:

```sh
cargo install --offline --locked --path tools/rsgraph \
  --root /home/sas/.containers/containers/devtools/.local
cargo clippy --offline --locked --manifest-path tools/rsgraph/Cargo.toml -- -D warnings
```

Сборка и Clippy выполнялись внутри devtools. Путь и SHA-256 установленного
бинаря — `runtime.json`; журнал Clippy — `rust-clippy.txt`.
Live hostrun rust-metadata на tests/fixtures/codegraph/rust/Cargo.toml
выдал protocol v1, 6 узлов и 3 ребра (`rust-live.json`).

Команда функционального повторения на интеграционной ревизии 182a1f97:

```sh
HOSTRUN_CWD=/home/sas/Projects/sp_aconsole_prototype hostrun test core \
  tests/core/test_codegraph_rust.py tests/hostrun/test_rust_metadata.py \
  tests/core/test_codegraph_protocol.py tests/core/test_code_index_cache.py
```

Все 45 тестов прошли, hostrun завершился с кодом 0 (`rust-tests.txt`).
Проверены реальные syn-фикстуры mod/use, неразрешённые внешние имена,
разделение crate/module namespace и совместимость протокола.

При попытке продолжить исходный pipeline на базе 41d8fca9 применён только
scope-delta реализации 7083f812. Ядро вернуло SCOPE_VIOLATION для всех новых
файлов tools/rsgraph. В frozen paths узла и родителя записан точный путь
`tools/rsgraph`; документ §6 требует `tools/rsgraph/**`. Точный путь не
разрешает запись потомков. Кроме того, подготовка 042711e5 меняет protocol.py
и test_codegraph_protocol.py вне существующего scope IDX-3.

Сформирован штатный REPLAN_INTERRUPT, запрос
**663fc0517db146268f54d22d3f**, узел возвращён в blocked. Запрос и причина
сохранены в `idx3-inbox.json`. Старый запрос d081cc7a23b5497d9a0abe97b6
имел решение unblock; оно не меняет frozen paths и не устраняет причину.

Следующее необходимое действие — явный ремонт существующих frozen-контрактов
IDX-3/IDX-0 с аудитом и пересчётом хэшей, затем свежая pipeline-приёмка.
Текущее plan/applyChange добавляет новые контракты и не разрешает заменять
существующие; repair_gate_profiles предназначен для других полей и не решает
эту ошибку. Прямые записи в БД и ослабление проверки scope не применялись.

## Интеграционные проверки

Полный production basedpyright на 182a1f97: 0 errors, 0 warnings, 0 notes
(`types-current.txt`). Первичный интеграционный прогон 312 passed, 1 skipped
и исправление старого argv-теста документированы в
`../plan-review-2026-11-17/REPORT.md`.

Изменения source после 182a1f97 — только два уточнения docstring; runtime
логика, UI и лимиты проверок не менялись. Временные scoped-срезы возвращены
к текущей ветке; основной checkout main не откатывался. Установка нового
DEB и перезапуск установленного ядра в эту приёмку не входили: live hostrun
проверяет экстракторы и исходники интеграционного checkout.
