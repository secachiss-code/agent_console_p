# Human-приёмка IDX-4 и LOD-3 — 2026-11-19

Пользователь подтвердил приёмку обоих ожидающих review узлов сообщением
«Прими» после отчёта, называвшего IDX-4 и LOD-3. Оба имеют
review_required=true. Завершение выполнено публичным native CLI
plan/setNodeStatus DONE с note, содержащей точное подтверждение пользователя.
Ответы сохранены рядом. Прямых записей в БД не было.

После истечения аренды оба узла автоматически вернулись в pending.
Первый вызов DONE получил GATE_RECEIPT_REQUIRED. Для корректной приёмки
каждый узел повторно claimed текущей вкладкой и переведён в verifying
на точном ранее проверенном checkpoint; штатная проверка подтвердила
сохранённые зелёные Micro/Blast. Новые pipeline-попытки не создавались,
контракты и gate profiles не менялись. Затем human DONE успешно выполнен.

IDX-4: attempt3, fence3, snapshot
refs/aconsole/attempts/node-905/3/3/03fb3ba6c3e6a172fd86419bf47b939e388b8ab9.
Micro865f55c3818894ac7e7ec4504882b8ca904651c8694d60e36c17a0d94300cf45,
Blast860dd91268c8d909c26d5700d2d9208f4d9dab30f754ba8c10177e1865041f23.
State e264fa45769c3320dd043cc66e099148f273df3c24a04c56e9e9fbb61eba46dd.
Код экстрактора и scoped-тесты совпадают с текущей веткой. Единственный
scoped diff — последующие дополнения отчёта csgraph-aot.md о результатах
проверок; production-код не менялся. Принят опубликованный DLL/dotnet exec,
Native AOT остаётся необязательным документированным спайком.

LOD-3: attempt2, fence4, snapshot
refs/aconsole/attempts/node-910/2/3/5c2960903daa0f304c34ea3107de33038b3d6744.
Microb8586f8d585332b55ff539c8c681491bc43017ec78fd648ad2af903e29c096a6,
Blast345ce650a25a9d99b4d4412be8288157c29a2dcebe2d0cf6551b61a787c14475.
State5dcc55b847062ca65bcc4bc7e40c5cf43625e285592068e961ee9a406565f31c.
Scoped-тест совпадает с текущей веткой побайтово.

После приёмки рабочая ветка ac/s-codex-bfd8f876482ec1cf восстановлена
на прежний HEAD045150f8, реализация последующих LOD/VLT узлов сохранена.
Доска:11done12pending0verifying0blocked. MEM-2 стал ready.
Ни одно направление L0 не закрыто: они требуют собственной direction
квитанции с настоящим image digest. Версия/основная установка не менялись.
