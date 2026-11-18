# LOD-3 — стабильность префикса

Коммит 8f8cbb6c, node-910, plan-900, fence3, attempt2.
26 тестов шейпера прошли; ruff format/lint и basedpyright чистые.
Два разных тела Python с одной сигнатурой дают одинаковые UTF-8 байты
префикса и SHA; смена аргумента меняет оба. Проверяются скелет файла scope
и d1 зависимости. file_slice меняется вместе с телом. Перестановка scope,
skeletons и dependencies не меняет prefix bytes/SHA.
Реализация уже удовлетворяла контракту, изменён только тестовый файл.

Micro 5/5: b8586f8d585332b55ff539c8c681491bc43017ec78fd648ad2af903e29c096a6.
Blast 2/2: 345ce650a25a9d99b4d4412be8288157c29a2dcebe2d0cf6551b61a787c14475.
State обеих квитанций:
5dcc55b847062ca65bcc4bc7e40c5cf43625e285592068e961ee9a406565f31c.

Первая попытка начата до фиксации LOD-2 из-за ошибки git add на tracked
папке cache, попадающей в ignore. Коммит затем выполнен git add -u.
Повторный claim не меняет базу running-попытки. Публичный scope repair
сузил LOD-3 до тестового файла, убрав не изменяемую реализацию. Это штатно
abandon старой цепочки и новая attempt2 от 16e98dbc, без расширения scope
и без ослабления gates. Старые попытки и repair request/response сохранены.
Native human CLI использован для административного repair/resume.

LOD-3 оставлен verifying: read-only проверка plan_nodes подтверждает
review_required=true. node_update done отклонён NODE_TRANSITION_INVALID;
human acceptance не подменялась. Пользователю отправлен конкретный запрос
приёмки после успешных проверок. Ответа на момент отчёта нет.

Доска: 8 done,14 pending,1 verifying,0 blocked. IDX-4 после истечения
аренды автоматически pending; его attempt3 Micro/Blast остаются зелёными,
обязательная приёмка человеком по-прежнему не получена.
Следующий независимый узел VLT-2; LOD-0 ждёт приёмки LOD-3 и direction
receipt с настоящим image digest. Версия пакета не менялась.
