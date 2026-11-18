# LOD-2 — кэш по interface_hash

Узел node-909, plan-900. CACHE_SCHEMA=2, ключ ast_skeleton_cache —
(interface_hash, extractor); file_sha256 удалён. cache_engine уже умеет
пересоздавать несовместимый файл, дополнительных путей миграции не нужно.

Регрессии: schema1 и будущая schema3 теряют старую таблицу и scratch;
повторное открытие актуальной версии сохраняет scratch. Два реальных
Python-файла с разными body_hash и одним интерфейсом делят одну строку;
смена сигнатуры и версии экстрактора разделяет строки. LRU/SQLITE_FULL,
перезапуск scratch, повреждённый файл и 10000 upsert проверены.

Попытка1: Micro 5/5, Blast 1/2. Из 392 зависимых тестов единственный
сбой — test_persistence_a.py:140 с жёстким ожиданием user_version=1.
Штатный plan/save repair_paths расширил scope LOD-2 и LOD-0 этим файлом,
граф и проверки сохранены; запрос/ответ рядом. Native IPC выполнялся
из host CLI со штатным handshake, прямых записей в БД нет.
Версия в старом тесте теперь сравнивается с CACHE_SCHEMA; ошибки строгой
типизации исправлены без подавлений. Чтение имён таблиц — через inspector.

Попытка2: локально cache_db+persistence_a 14 passed, ruff и basedpyright
чистые; L2 patch/test/verify и Micro 5/5 зелёные.
Micro receipt 3df8506d573891c41e76abdbf078810ea2cc714217f038389a4f6f5a22ab0346.
Blast attempt2: 2 passed, 0 failed; 14 тестов узла и 392 зависимых
теста прошли. Receipt cc2a0d91c3067628b304421564dba568e863eddd99757eb648b64e1e6a5a8470.
State b05f19259123c545af779b22fbcccfebc73d06411a83eae1ecd56b226a4b9c34
совпадает у Micro и Blast. Узел штатно закрыт verifying → done,
review_required=false. Реализация 16e98dbc. Транспортный лимит MCP 60 секунд
не равен провалу; итог прочитан по сохранённой квитанции через mode=ro.
