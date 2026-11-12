# GIT-5: неполный результат commit-tree

База: `e6bcd572cd5964a90dbb395064a256daef8f606f`.

Fault injection QA-14 выявил ложное подтверждение слияния без рабочей копии на
целевой ветке. При `GitDone(ok=True, oid=None)` от `commit_tree` сервис возвращал
этот ответ как успешный результат. `_ended` подставлял прежний target OID и
записывал finished, хотя целевой ref не двигался; follow-up мог запуститься.
Штатный Git такого ответа в этих проверках не выдавал. Это проверка обработки
неполного результата порта, а не свидетельство наблюдавшегося сбоя Git.

Четыре регрессионных варианта: merge/squash × пустое/непустое сообщение порта.
Все до исправления **FAIL / 6.88 s**, ожидаемый failed оказался finished
(`before-regression.xml`). Сценарии используют настоящий daemon RPC, Git,
SQLite, миграции и журнал; подменяется только ответ `commit_tree`.

Теперь неполный успешный ответ преобразуется в `GitDone(ok=False, ...)`.
Сообщение порта сохраняется; при его отсутствии используется
`git commit-tree returned no commit id`. Обновление ref и follow-up не выполняются.
Тесты проверяют failed без result OID/pending follow-up, неизменные target/HEAD
и чистый worktree. Повтор с новой preview и настоящим Git завершается успешно;
после рестарта доступны и прежняя failed-запись, и finished результата повтора.

Все 12 core-файлов `test_git*.py`: **110 PASS / 75.53 s** (`focused.xml`), включая
новые четыре регрессии, QA-14 и существующие merge/rebase/worktree-тесты.
Ruff, `git diff --check` и строгая типизация изменённого продуктового модуля:
**PASS**, basedpyright **0 errors, 0 warnings, 0 notes**. Для проверки типов
использован временный JSON, расширяющий pyproject с существующей host `.venv-ui`
и `include` на git_merge.py. Первая попытка использовала исчезнувший временный
конфиг предыдущего этапа и была прервана; успешный результат получен после
явного создания нового конфига.

Полный core/contracts/gate/hostrun/smoke/packaging: **2143 PASS, 1 SKIP,
1 performance DESELECT, 27 warnings / 418.03 s** (`core.xml`).

Свежий полный core + сохранённый GUI неизменённых модулей: **89.832668%**,
26677 строк + 5749 выходов ветвей из 28988 statements + 7108 branch exits,
279 файлов. До 90% остаётся **61 единица**. Настоящий `coverage report`:
**exit 2**, 89.83 < 90.00; QA-4 остаётся FAIL. Это новый замер изменённого
продукта; прирост не приписывается исключительно регрессионным тестам GIT-5.

## Воспроизведение

```sh
/path/to/.venv-core/bin/python artifacts/git5/run.txt --new-only
/path/to/.venv-core/bin/python artifacts/git5/run.txt
/path/to/.venv-core/bin/python artifacts/git5/run.txt --full
/path/to/.venv-core/bin/python artifacts/git5/summarize.txt
```

В этой среде тесты запускаются через `distrobox-host-exec`.
Обычный run включает все core `test_git*.py`, `--new-only` — новые четыре сценария.
Summarize объединяет свежий полный core и сохранённый полный QA-9 GUI,
предварительно удаляя данные GUI для изменённых knowledge.py и git_merge.py.
Проверяется точный список изменённых продуктовых файлов относительно обеих баз.
Это не свежий полный GUI/CI-прогон. GUI, performance и deb install не повторялись;
порог 90% и независимый gate сохраняются.
