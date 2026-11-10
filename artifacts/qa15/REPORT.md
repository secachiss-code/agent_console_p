# QA-15: отказы интеграции и очистки worker results

Продукт: WS-1 `d5902e6e` (база до найденного дефекта: GIT-5 `9da08b2b`).

12 новых сценариев через настоящий daemon RPC, Git и SQLite:

- Интеграция: открытая основная вкладка, untracked файл, staged файл,
  незавершённый merge, неверный source OID, попытка интегрировать begin snapshot.
  Preview и выполнение отказывают, HEAD/refs и файлы обеих сторон сохраняются;
  после устранения причины свежая preview позволяет интеграцию.
- Очистка принятого результата: открытая вкладка на worker path, untracked файл,
  игнорируемый файл, незавершённый merge, отказ Git remove_worktree и отказ журнала.
  До записи журнала удаление не начинается. Ошибка удаления записывается в
  реальную SQLite; после restart failed-запись доступна по operation ID.
  После устранения причины и restart повтор успешен; ignored файл удаляется
  только при явном discard_ignored=True. Snapshot ref и основной результат остаются.

Git refusal — fault injection ответа порта, journal refusal — fault injection
ответа save. Это проверки обработки отказа, а не наблюдение самопроизвольного
отказа штатных Git/SQLite. При Git refusal текущий RPC возвращает INTERNAL_ERROR,
в журнале сохраняется failed с сообщением `worker worktree was not removed`.
Завершённые операции читаются из SQLite, а не из списка открытых операций.

При разработке сценариев обнаружен и отдельно исправлен **WS-1**: restart
изолированной вкладки перезаписывал primary WorkSession. Четыре отдельные
регрессии до исправления FAIL, после исправления PASS; отчёт artifacts/ws1/REPORT.md.
Первоначальные черновые проверки также содержали неверные допущения о begin
без launchSpec, способе выбора existing worktree, публичном списке только
открытых операций и коде Git-ошибки. Их промежуточные отказы не считаются
продуктовыми регрессиями. Воспроизведение WS-1 сохранено отдельно.

Связанные test_work_session*.py + test_work_sessions.py + новые сценарии:
**26 PASS / 43.06 s** (`focused.xml`). В этот набор входят прежние проверки
подмены snapshot ref и сохранности позднего worker commit. Ruff и diff check PASS.

## Воспроизведение

```sh
distrobox-host-exec /home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/qa15/run.txt
distrobox-host-exec /home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/qa15/run.txt --full --baseline
distrobox-host-exec /home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/qa15/summarize.txt
```

--new-only запускает только 12 новых сценариев; обычный runner — 26 связанных.
--full --baseline запускает полный non-performance core/contracts/gate/hostrun/
smoke/packaging без нового файла QA-15. Вместе с focused.xml это весь набор
в двух группах; сравнение baseline/final coverage выполняется на одном src.
Summarize проверяет отсутствие изменений продукта относительно WS-1, список
изменений относительно QA-9 GUI и удаляет старые GUI данные трёх модулей:
knowledge.py, git_merge.py, tabs.py. Старый GIT-5 baseline нельзя прямо сравнивать
с текущим продуктом из-за изменения tabs.py в WS-1.

Полный GUI, CI, performance и deb install не запускаются этим runner.
Независимый выпускной gate и остальные требования QA-4 остаются обязательными.

## Полный набор и покрытие

Полный baseline non-performance core/contracts/gate/hostrun/smoke/packaging:
**2147 PASS, 1 SKIP, 1 performance DESELECT, 27 warnings / 422.82 s**
(`core-baseline.xml`). Включает все четыре регрессии WS-1. Вместе с 12 новыми
сценариями из успешного focused прогона проверено **2159 уникальных тестов**;
полный набор разделён на две группы, а не выполнен одной командой.

На одном и том же текущем src, свежий полный core + QA-9 GUI неизменённых модулей:

- Без QA-15: **89.80219414893617%**.
- С QA-15: **89.86314273049645%** (+14 покрытых строк и +8 branch exits).
- 26682 covered lines + 5755 covered branch exits из 28988 statements + 7108 branch exits.
- До 90% остаётся **50 единиц покрытия**. Настоящий `coverage report`:
  **exit 2**, 89.863143 < 90.000000; округлённое percent_covered_display=90 не означает PASS.

QA-4 остаётся **FAIL**. Это не новый полный GUI или CI. Старый GIT-5 результат
89.832668% не используется для оценки прироста новых тестов: продукт изменился,
а удаление старых GUI данных tabs.py уменьшило доступное покрытие этого модуля.
27 предупреждений полного набора сохранены в отчёте pytest; их отсутствие не заявляется.
