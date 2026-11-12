# QA-14: отказы журнала и восстановление Git-операций

База `9a9c0bc15963cad042adc7f59e306e60da33411b`, продукт и coverage config не менялись.

12 новых сценариев вызывают настоящий daemon через Unix RPC, используют реальные
Git-репозитории, refs/worktree, SQLite, миграции и журнал операций:

- отказ записи начального journal record блокирует Git как с рабочей копией на
  целевой ветке, так и без неё; новая preview после восстановления допускает merge;
- отказ merge hooks, создания commit и обновления ref не меняет целевой ref,
  основной HEAD или рабочую копию и оставляет долговечный failed record;
- отказ follow-up после настоящего merge и отказ записи после начального record
  сохраняют реальный результат; retry завершает bookkeeping, не повторяя merge,
  а открытие нового ядра подтверждает finished и правильный result OID;
- при рестарте незавершённый cleanup существующего каталога становится failed,
  отсутствующего/неуказанного — finished; удалённая целевая ветка и независимо
  сдвинутая ветка дают failed с разными причинами, не меняя Git refs или файлы.

Отказы инъецируются на портах записи журнала и GitWriter. Физический отказ диска
не воспроизводился; успешные merge, retry и повторное открытие базы реальные.

Новые: **12 PASS / 16.96 s**, `recovery.xml`. Связанные merge/cleanup:
**27 PASS / 31.19 s**, `focused.xml`. Ruff и `git diff --check` PASS.

На неизменном продукте добавлены **14 строк + 7 выходов ветвей**.
Baseline MCP-1 full core + сохранённый QA-9 GUI дополнен только новым прогоном:
**89.762274% → 89.820459%**, 26673 строки + 5745 выходов ветвей из неизменных
28986 statements + 7106 branch exits. До 90% остаются **65 единиц**.
Настоящий coverage gate: **exit 2**, 89.82 < 90.00; QA-4 остаётся FAIL.

Пробный запуск: 7 PASS, 6 FAIL. Пять ошибок были обращением теста к отсутствующему
полю MergeProbeView.tree; исправлена тестовая запись восстановления (merge
распознаётся по родителям). Шестой сценарий обнаружил отдельный дефект: успешный
commit-tree без OID приводит к ложному finished. Он перенесён без ослабления
ожиданий в `test_git_missing_commit_oid.py`, отдельный узел GIT-5. Это результат
fault injection; штатный Git пустой успешный ответ в этих прогонах не выдавал.

Воспроизведение из checkout с core Python окружением через `distrobox-host-exec`:

```sh
/path/to/.venv-core/bin/python artifacts/qa14/run.txt --new-only
/path/to/.venv-core/bin/python artifacts/qa14/run.txt
/path/to/.venv-core/bin/python artifacts/qa14/summarize.txt
```

Не повторялись полный core/GUI, CI, performance, deb install и независимый gate.
