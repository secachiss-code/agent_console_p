# GATE-2 — подготовка независимой приёмки GATE-1

GATE-2 подготовлен; **GATE-1 не принят**, доверенный PASS не включён.
QA-6 остаётся failed, CMT-2 — pending. Runtime, policy, установленный пакет,
mounts и пользовательские сессии не менялись.

## Результат

`ACCEPTANCE.md` задаёт конкретную отдельную среду приёмки, закреплённые inputs,
immutable verifier/baseline, независимую identity reviewer, worker без секретов,
атомарный ledger, одноразовые request IDs и матрицу проверки положительной ветки,
подделок, self-approval, replay и отказов. Это проект протокола: доверенной среды,
верификатора, ключей, ledger и независимого решения пока нет.

`prepare.py` экспортирует только committed blobs, без исполнения candidate-кода,
Git filters и archive substitutions. Детерминированный ZIP сохраняет executable bits.
Symlink/gitlink отвергаются, существующий каталог не перезаписывается. SHA256
архива и каждого из 1233 файлов сохранены в `candidate.json` вместе с Git OID/mode.
Источник — `2eea8a1f596b4aaa490c611f727e299efabdbb54`, tree
`63bf8cce439a49ec1865e604ce2f5606f8f476c0`.

Локальный архив: `/tmp/aconsole-gate1-candidate-2eea8a1f/candidate.zip`.
SHA256: `ba3bf1372b5331727147ff45f2a7721b875cf2f75aeaf8ef9e165bd5e0606283`.
Он не включён в Git и может исчезнуть после очистки /tmp. Манифест и exporter
сохранены; исторический архив воспроизводится через `--revision` указанного HEAD.
Новые файлы самого пакета `artifacts/gate1` в snapshot продукта не входят,
их отдельные hashes фиксирует `evidence.json`.

## Проверки

- `python3 -m unittest artifacts.gate1.test_prepare -v`: 7 tests, exit 0. Проверены
  committed bytes при staged/unstaged/untracked изменениях, export-ignore/subst,
  воспроизводимость текущей и исторической ревизии, modes/hashes, запрет overwrite,
  symlink и gitlink. Исходные 6 tests прошли до добавления `--revision`;
  7 tests пройдены после расширения exporter.
- Текущий worktree: абсолютный `.venv-core/bin/python` основной копии как
  интерпретатор, `-m pytest tests/hostrun -q -p no:cacheprovider
  --junitxml=artifacts/gate1/hostrun.xml`: **218 passed, 1 skipped**, exit 0.
  Interpreter — Python 3.15.1 free-threaded; rootdir и тесты — текущая ветка.
  Для локальных Unix sockets разрешена exec-эскалация; запуск остаётся внутри
  агентского контейнера, не является независимым security review.
  Штатный skip — linked docs отсутствуют (`test_client_lists_match_known_actions`).
- Ruff новых Python-файлов и `git diff --check` проходят. Старые 6193 artifacts
  lint findings не исправлялись; общий tools/check.sh заново не запускался.

Первый `hostrun test hostrun` завершился exit 0, но audit показал маршрут в
`/home/sas/Projects/sp_aconsole_prototype`, HEAD основной копии при осмотре
`0b327458cb66207ac9585703269e536fa5b179f7`. Этот результат **не засчитан текущей
ветке**. Из-за обнаруженного несоответствия выполнен отдельный прогон текущего
worktree выше. Точная команда, audit и оба результата записаны в `checks.json`.

Проверка пакета:

```sh
python3 artifacts/gate1/verify.py /tmp/aconsole-gate1-candidate-2eea8a1f/candidate.zip
```

Без аргумента проверяются retained evidence и текущие исходники, без проверки
удаляемого локального ZIP. Это проверка целостности недоверенного транспорта,
не проверка независимости и не выдача PASS.

## Следующий шаг

Выделить независимую среду и reviewer по `ACCEPTANCE.md`, реализовать и проверить
доверенный coordinator/verifier/ledger и только затем обсуждать активацию.
До этого сохранён существующий fail-closed механизм. Для QA-6 также остаются
Cursor tool baseline, TD-SEC7 и причина двух прежних SIGTERM143 полного collector;
этот короткий hostrun-прогон их не разрешает.
