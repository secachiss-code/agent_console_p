# QA-16: границы чтения Git через RPC

Исходники продукта неизменны относительно `9c9103808e3056bea2ad6b3325a96dc32ba9b20a`.
Добавлены 17 сценариев через настоящий daemon RPC и Git:

- Пустой репозиторий, staged diff до первого commit и diff корневого commit.
- Бинарный, пустой, symlink и FIFO untracked paths: симлинк не раскрывает содержимое
  внешнего файла, FIFO не блокирует чтение; Git не включает FIFO в summary.
- Byte cap для tracked и untracked diff: DIFF_TOO_LARGE сохраняет файл и HEAD,
  последующий небольшой diff успешно читается. Используется штатный лимит 2 МБ.
- Лимиты строк 0/3/100, отрицательный лимит, сравнение двух OID и base с явным
  и неявным revision, отсутствующий OID, разнесённые hunks и бинарный commit.
- Graph вкладки исключает независимую ветку, показывает tag/remote/detached HEAD,
  отклоняет неизвестную вкладку и неверный/устаревший cursor.
- Graph и state сессии исключают другую сессию; graph изолированных результатов
  сохраняет sequence/actor/previous_snapshot и фильтр вкладки.
- После удаления сохранённой default branch state выбирает доступную базу:
  текущую ветку либо local counterpart origin/HEAD.
- Четыре Git read RPC отклоняют неизвестный project_id, даже когда указан путь
  существующего репозитория; последующий запрос известного проекта успешен.

Проверяются возвращённые данные, неизменность файлов, Git objects, HEAD/refs и
состояния рабочей копии. Настоящие CLI агентов не запускаются; tab/launchSpec
резервирует запуск, как в существующих WorkSession RPC-тестах.
Отсутствующий OID сейчас даёт INTERNAL_ERROR: это зафиксированное поведение,
а не утверждение о качестве пользовательского сообщения.

Первый прогон: 34 PASS / 1 FAIL из-за ошибочного ожидания полностью чистого
worktree после tab/open. Служебный `.aconsole` уже существовал до чтения Git.
Проверка исправлена на сравнение состояния до/после; продукт не менялся.
Промежуточный отказ не является продуктовой регрессией.

Итоговый связанный набор: **40 PASS / 28.73 s**, включая все 17 новых сценариев
(`focused.xml`). Ruff и `git diff --check` PASS.

## Покрытие

На одном src, QA-15 baseline и новый связанный прогон:

- До: **89.86314273049645%**, coverage gate exit 2.
- После: **90.0072030141844%**, настоящий `coverage report --precision=6` exit 0.
- Прирост: **33 строки и 19 branch exits**.
- 26715 covered lines + 5774 covered branch exits из 28988 statements + 7108 branch exits.

Порог 90% и scope исходников не менялись. Запас над порогом очень мал.
Это объединённые локальные данные: полный QA-15 core baseline и его focused
прогон, сохранённый QA-9 GUI только неизменённых модулей и новый QA-16 focused.
Старые GUI-данные knowledge.py/git_merge.py/tabs.py удалены ещё в QA-15.
Скрипт проверяет отсутствие diff src/pyproject относительно `9c910380`.
Это **не свежий полный GUI и не CI**. Новый полный core также не запускался:
полный baseline продукта уже был проверен QA-15, здесь добавлены новые сценарии.
QA-4 остаётся FAIL до фактического успешного CI и выполнения остальных требований,
включая performance/deb install и независимую приёмку gate.

## Воспроизведение

```sh
distrobox-host-exec /home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/qa16/run.txt
distrobox-host-exec /home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/qa16/summarize.txt
```

`--new-only` запускает 17 новых тестов; default — 40 связанных.
`--full` запускает полный non-performance core/contracts/gate/hostrun/smoke
и пакетные проверки. Этот режим в QA-16 не выполнялся.
Сырые coverage/log inputs сохраняются локально в игнорируемых файлах.
