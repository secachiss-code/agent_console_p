# VLT-2 — защита чужого тела заметки

Узел node-913 плана plan-900, fence3, attempt3. Замороженный scope
штатно расширен VaultFileDatabase и старым Canvas-тестом, уже входящими
в родительский VLT-0. Первые две цепочки административно abandoned
после аудируемого repair_paths, gates до исправления scope не запускались.

Сравнение до любой записи: blake2b тела вне app-секции «Попытки».
Frontmatter проходит прежний merge. При чужом теле не вызываются ни полная
перезапись, ни update_attempt: файл и mtime сохраняются, попытки остаются
в node_attempts. pending_write хранит отложенный текст заметки, успешные
hashes и base_frontmatter не заменяются. После снятия конфликта очередной
publish восстанавливает актуальные поля и все попытки из durable history,
сбрасывает pending_write.

Хэш защищённого тела хранится в дополнительном поле существующего JSON
base_frontmatter; миграций нет. Старые публикации без этого хэша допускают
только точное совпадение полного body_hash. Упрощённые BaseStore используют
baseline RAM; неизвестная существующая заметка сохраняется консервативно.
Markdown-заголовки в fenced code examples не становятся app-секцией;
соседние секции и точные окончания строк остаются защищены.

VaultWriter проверяет expected bytes перед заменой. Конфликт чтения/записи
откладывает также history loop до следующего merge: front-only правка не
поглощается успешной записью попытки. Это проверка перед записью, а не
заявление о файловом CAS с другими процессами.

Регрессии: foreign paragraph, повтор publish, новый VaultSync/новый store,
сохранение NodeAttempt и baseline, pending_write и его очистка, восстановление
после снятия конфликта, редактирование только app-секции, legacy-публикации,
CRLF, fenced examples, expected-byte refusal и конкурентная front-правка.
Старый Canvas-тест теперь проверяет defer при чужом абзаце, затем публикацию
всех четырёх durable попыток; проверки сохранённых позиций и состава файлов
остались. Локально Writer+Canvas33 tests passed; до последних двух регрессий
общий Writer+Canvas+Wire42 passed. Ruff/format и basedpyright чистые.

Micro attempt3: 5 passed,0 failed.
Receipt c9558469fb93cb93be84485d31a91e33897558069d6cd352712aa8a6619513d2.
Blast attempt3: 2 passed,0 failed. 14 тестов узла и 344 зависимых теста.
Receipt cfb89a4efa670a83dbfa9528f99c67cba4fd1b84c36af8e5ce1d2c5f90ea5a73.
Обе квитанции относятся к одному state_hash:
6174cddc24dcd33d7c58c2639fd29cf25459b09cc376a926e59f3ae6f7da56fa.
Micro tests также проверил 44 теста. MCP Blast превысил транспортный60с
лимит; успех подтверждён сохранённой квитанцией и passed-попыткой mode=ro.

Реализация3529f52a. Узел штатно закрыт verifying → done.
Доска9done14pending0verifying0blocked. LOD-3/IDX-4 после истечения аренды
pending, их прежние успешные квитанции остаются; human-приёмки не было.
Следующий независимый узел — GAT-1 (предпроверка патча).

review_required=false подтверждён read-only SELECT по plan_nodes.
IDX-4/LOD-3 не приняты: «продолжай» не трактовалось как явная human-приёмка.
Версия пакета не менялась, L0 не закрывались, основная установка не обновлялась.
