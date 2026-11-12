# QA-17: свежая полная проверка QA-4

База: `3af391ab275b4e006660fbf2370cca14a7268941`, 2026-11-10.
Продукт, тесты и `pyproject.toml` не менялись. Все тяжёлые этапы выполнялись
последовательно на хосте с существующими окружениями Python 3.15.1 проекта.
**QA-17: проверка завершена; общий выпуск и QA-4 не приняты.**

| Этап | Фактический результат |
| --- | --- |
| Полный core/contracts/gate/hostrun/smoke и пакетные проверки | 2176 PASS, 1 SKIP, 1 performance deselect; 27 warnings; 464.63 s |
| Полный GUI/UI/smoke с branch coverage | 507 PASS, 1 прежний PERF-7 deselect; 413 warnings; 195.28 s |
| Core runtime без coverage | 1 PASS, 1712 deselected; 5.05 s |
| Исходный тест прокрутки 10 000 коммитов без coverage | 1 FAIL; 13.75 s; median 33.7 ms > 17.5 ms |
| `tools/check.sh` | PASS; 48.94 s |
| Сборка настоящего deb | PASS; 55.81 s; 0.1.1/amd64 |
| Проверка распакованного deb из `/tmp` | 4 PASS: core/UI import и GIL, UI/core `--help` |

Core был запущен существующим `artifacts/qa16/run.txt --full`. Его новые JUnit,
лог и raw coverage скопированы сюда без фильтрации; `core-source.json` фиксирует
происхождение. Прежние focused или GUI-данные в этом объединении не участвуют.
Остальные этапы используют `run.txt`; полный core в нём воспроизводит тот же
список наборов и фильтр performance. Хеши исходников и `pyproject.toml` в
`core-source.json` и `ui-source.json` сверены перед объединением.

## Свежее покрытие

Только текущие полные core и UI: **90.01828457446808%**.
Покрыты 26719/28988 statements и 5774/7108 branch exits. Настоящий
`coverage report --precision=6 --format=total` завершился с **exit 0**
(`coverage-gate.txt`). Scope и порог 90% сохранены; запас над порогом мал.
Это локальные данные, не результат GitHub Actions.

`coverage-summary.json` содержит точные totals. Полный JSON и raw `.coverage.*`
остаются локальными; `combine` воспроизводит их из двух свежих входов.

## Производительность и предупреждения

PERF-7 остаётся **FAIL**: 10000 rows, 108 frames / 4 s, median 33.7 ms,
p95 44.5 ms, worst 187.3 ms. RSS 382 MiB против 328 MiB на входе;
все строки загружены за 3.3 s. Лимиты 17.5 ms / 150 MiB / минимум 30 frames
не менялись. Проверка RSS в исходном тесте расположена после отказавшего
assertion median и в этом запуске не исполнялась; рост памяти — наблюдение.

GLX использует llvmpipe/Mesa, `Accelerated: no` (`ui-performance-gl.txt`).
Фактический класс GSK renderer отдельно не записывался. Аппаратная GPU-проверка
и сравнение производительности разных commits этим прогоном не выполнялись.
Общий `finish.txt` возвращает 1 из-за этого отказа, хотя остальные этапы PASS.

Предупреждения полных наборов сохранены в JUnit/локальных логах: в том числе
deprecated GTK API, forkpty в многопоточном процессе, незавершённые coroutine
`HealthDialog.refresh` и `MainWindow._renew_git_watch`. Это не прогон без warnings.

## Статические проверки и пакет

Ruff PASS; basedpyright 0 errors / 0 warnings; import-linter 6 kept / 0 broken;
JS/raw SQL/контраст/API floor PASS; 11 контрактных проверок PASS.
Две проверки `docs/contracts` штатно пропущены: linked docs в worktree отсутствуют.
Условная ownership-проверка в `check.sh` без `HOSTRUN_CONTAINER` не вызывалась;
аудит коммитов CMT-1 этим результатом не принимается. Отклонение запрещённой
API fixture ожидаемо и входит в успешный `check.sh` (`check-summary.txt`).

Собран `dist/aconsole_0.1.1_amd64.deb`. `package-smoke.json` содержит размер,
SHA-256, Debian metadata и результаты запуска настоящего содержимого пакета
из временного каталога вне checkout, без PYTHONPATH/PYTHONHOME.
Core работает без GIL, UI с GIL; UI импортирует gi. Core `--help` возвращает
ожидаемый код 2, UI — 0. Проверены Python entry points распакованного пакета.
Установка через apt/dpkg, maintainer scripts, системные launcher paths и
desktop integration этим smoke не проверялись. Пакет на хост не устанавливался.

## CI и оставшиеся условия

GitHub combined status не вернул checks, а fetch commit вернул 422
`No commit found for SHA` для текущей базы (`ci-lookup.json`). Текущего commit
на GitHub в момент проверки нет; push и новый CI в этом проходе не выполнялись.
Предыдущий успешный deb-install CI на другой базе не считается текущим PASS.

**QA-4 остаётся failed до фактического CI с combined coverage и установкой deb.**
PERF-7 также failed; SEC-11, независимый GATE-1, CMT-1 и явная пользовательская
приёмка DES-3 этим отчётом не закрываются. Сведения о нынешней проверке не
заменяют доказательств этих узлов.

## Воспроизведение

На хосте из корня данного worktree с существующими окружениями проекта:

```sh
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/qa4-final/run.txt core
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/qa4-final/run.txt ui
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/qa4-final/finish.txt
/home/sas/Projects/sp_aconsole_prototype/.venv-core/bin/python artifacts/qa4-final/package-smoke.txt
```

`finish.txt` последовательно выполняет combine, core performance, UI performance,
check и build; сохраняет каждый результат и не скрывает первый отказ.
UI использует приватный Xvfb, английскую локаль и изолированное состояние.
При отсутствии Xvfb runner извлекает deb во временный каталог, как QA-9.
Для check/build нужны `.venv-core` и `.venv-ui` данного worktree: при проверке
использовались временные симлинки на окружения основного checkout, удалённые
после завершения. HOME/XDG тестов изолирует существующий conftest.
