# FND-1 — транзакционный индекс фактов и заметок

Узел node-919, plan-900, fence3, attempt2; реализация `8abf4964`.
Узел завершён штатным node_update после проверки квитанций.
Доска: 14 done, 9 pending, in_progress/verifying/blocked нет.

Миграция 0013 создаёт note_fts с title/body/note_ref UNINDEXED и
токенизатором unicode61. DDL и обновление строк сосредоточены в
persistence/search.py; миграция делегирует создание и удаление этому модулю.
Сохранение факта обновляет FTS через ту же Session. DELETE-триггеры удаляют
строки индекса при удалении исходного kb_nodes/vault_files, включая каскад
удаления проекта. Downgrade снимает триггеры и виртуальную таблицу.
Существующие факты не переиндексируются миграцией; их добор прежним
поиском входит в FND-2 по §4.5 плана.

VaultSync передаёт успешно опубликованное тело в опциональный
PublicationStore. VaultFileDatabase сохраняет его индекс вместе с
frontmatter, digests и protected-body baseline в одном writer.call.
Конфликт чужого тела сохраняет прежний индекс, а pending_write его не
меняет. Файловая запись остаётся прежней: атомарность FTS относится к
транзакции метаданных публикации, не к общей транзакции SQLite/файловой системы.

Scope FND-0/FND-1 штатно расширен vault_files.py через native plan/save.
Attempt1 abandoned до gates; attempt2 использует исправленный контракт
978d0eadb87cec706da99fc865f7d7931d3abecc6671800ce358806635efb13b.
Запрос и ответ ремонта сохранены рядом. Прямых записей в БД не было.

Локальная регрессия:
`.venv-core/bin/python -m pytest -o addopts='' tests/core/test_knowledge_store.py tests/core/test_merge_migrations.py tests/core/test_vault_writer.py tests/core/test_vault_wire.py -q`
— 35 passed. Ruff check/format, strict basedpyright восьми изменённых
файлов и tools/check_no_raw_sql.py чистые. SQLite 3.53.1 текущего
.venv-core подтверждает ENABLE_FTS5.

Тесты проверяют Unicode/case/diacritics MATCH, видимость до commit и
rollback факта/FTS, удаление и откат удаления, каскад удаления проекта,
rollback индекса и baseline при ошибке публикации, замену без дубликатов,
неиндексирование pending_write, реальную публикацию VaultSync и отказ при
чужом теле, downgrade/re-upgrade с сохранением исходных фактов.

Micro: 5 passed, 0 failed; 31 тест.
Receipt: 2a590e6584d7ce281c2e1c1a10a6dc0c614e736e6de96ba343011732e322b80c.
State: f8c44747dcffa34b080019d44e4d30ba9e74db71eb5ce364c7c394a873bf83aa.

Blast: 2 passed, 0 failed; 10 тестов команды узла и 318 зависимых тестов.
Receipt: 045dc12a3116f8e151edfebd17030b4ba41fc58a01dea564b389b37e76a36e78.
Обе квитанции относятся к одному state_hash выше. Вызов verifying превысил
MCP transport timeout 60 s; исходная проверка завершилась в ядре (216,868 s).
Сохранённые зелёные receipt/attempt проверены через mode=ro, повторного
запуска gates не было. Последующий node_update done принят штатно.

L0 и выпуск не закрываются. Настоящий devtools image digest, direction
receipt и human acceptance остаются условиями L0. Следующий узел FND-2:
порядок BM25 в ctx_get, malformed MATCH → empty и legacy fallback.
