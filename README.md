# AConsole

**AConsole** — настольная консоль для работы с CLI-агентами. Она запускает Claude Code,
Codex CLI, OpenCode и Cursor CLI в контейнерах Podman, объединяет исполнителей в рабочие
сессии задач и добавляет поверх терминала то, чего обычно не хватает: согласования опасных
команд, доску планов с MCP-инструментами, чекпоинты, страницу Git для review и merge,
а также панель лимитов и расходов по подпискам.

[![check](https://github.com/secachiss-code/agent_console_p/actions/workflows/check.yml/badge.svg)](https://github.com/secachiss-code/agent_console_p/actions/workflows/check.yml)
[![release](https://img.shields.io/github/v/release/secachiss-code/agent_console_p?display_name=tag&sort=semver)](https://github.com/secachiss-code/agent_console_p/releases)
[![platform](https://img.shields.io/badge/platform-Linux-lightgrey)](https://github.com/secachiss-code/agent_console_p)
[![status](https://img.shields.io/badge/status-prototype-orange)](https://github.com/secachiss-code/agent_console_p)

> **Статус: рабочий прототип.** Проверен только на Linux-машине автора. API, поведение и
> формат внутренних данных могут меняться. Это не песочница и не замена контейнерной
> изоляции: агент работает с правами пользователя внутри контейнера.

## Версия

| Что | Значение |
|---|---|
| Версия | **0.1.1 «Работа с Git»**, опубликована 2026-11-06 |
| Тег и коммит | `v0.1.1`, `45152a1` |
| API контрактов | `1.4.0`, протокол IPC `1.0` |
| Пакет | `aconsole_0.1.1_amd64.deb`, 67 586 408 байт, SHA-256 `23b001936f03cc2f15c9ffdcc6fbc0b68235d212b817eea8e8bf44a2ded2de16` |

Версия в `pyproject.toml`, `src/aconsole/__init__.py` и ответе ядра — `0.1.1`.
Предыдущий релиз — `0.1.0` (2026-11-04). История версий — в [CHANGELOG.md](CHANGELOG.md).

## Возможности

- **Проекты и рабочие сессии задач.** Проект содержит сессии; сессия владеет основной
  рабочей копией, веткой и историей задачи. Вкладка — исполнитель, а не владелец каталога.
  Три режима основной копии: новый worktree `ac/s-<slug>-<id>`, основной checkout,
  существующий worktree.
- **Эстафета и изоляция.** В общем каталоге работает один агент; передача управления
  подтверждается и проверяет процессы. Параллельные исполнители получают detached worktree,
  их результат принимается в сессию, затем сессия сливается в базовую ветку.
- **Четыре агента** в общих контейнерах `agent-claude`, `agent-codex`, `agent-opencode`,
  `agent-cursor`. Resume — Claude, Codex и OpenCode; у Cursor ядро умеет `--resume`, но
  идентификатор нативной сессии в 0.1.1 получить неоткуда. Форк — только Claude и Codex.
- **Терминал VTE** с поиском, масштабированием, буфером обмена и вставкой PNG через ядро;
  между VTE и агентом стоит sidecar на стандартной библиотеке Python.
- **Согласования** опасных инструментов Claude и Codex: риск-теги, разовые и сессионные
  разрешения, allowlist по префиксу, карточка поверх терминала, системные уведомления.
- **Планы, MCP и база знаний.** Двенадцать MCP-инструментов, DAG узлов с lease и fence,
  синхронизация с Obsidian-vault, граф кода для `kb_impact`.
- **Чекпоинты** на side-refs с приватным индексом, preview и одноразовым confirm-токеном,
  историей этапов сессии и защитными снимками.
- **Страница Git:** состояние worktree, граф коммитов и чекпоинтов, diff по хункам,
  stage/unstage/commit, merge/rebase/cherry-pick с preview, очистка worktree, отправка
  diff другому агенту.
- **Лимиты и расходы** по Claude, Codex, Cursor и OpenCode, включая окна квот, деньги
  и стоимость сессии.

## Требования

| Что | Требование |
|---|---|
| ОС | Linux с X11 или Wayland |
| GTK | GTK 4.14+, libadwaita 1.5+, VTE 3.91 и GObject introspection |
| Git | обычный Git (используются worktree и side refs) |
| Контейнеры | Podman и контейнеры `agent-*`; рекомендуется Distrobox |
| Агенты | CLI установлен и залогинен внутри своего контейнера |
| Python | для запуска из исходников — `uv`, Python 3.15t (ядро) и 3.15 (UI); в `.deb` Python внутри |

AConsole **не создаёт контейнеры и не устанавливает CLI агентов**. Каталоги worktree и
vault должны быть смонтированы в контейнеры по тем же путям, что и на хосте.

## Установка

### Из GitHub Release (.deb)

```sh
wget https://github.com/secachiss-code/agent_console_p/releases/download/v0.1.1/aconsole_0.1.1_amd64.deb
sha256sum aconsole_0.1.1_amd64.deb
# 23b001936f03cc2f15c9ffdcc6fbc0b68235d212b817eea8e8bf44a2ded2de16

sudo apt install ./aconsole_0.1.1_amd64.deb
aconsole
```

Пакет ставит `/usr/bin/aconsole` (UI), `/usr/bin/aconsoled` (ядро), собственные Python 3.15t
и 3.15 под `/opt/aconsole`, `.desktop` и иконки. Собран для Debian/Ubuntu `amd64`,
не подписан GPG — сверяйте SHA-256.

### Из исходников

```sh
sudo apt install gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-vte-3.91 \
  libgirepository-2.0-dev libcairo2-dev git podman
uv python install 3.15t 3.15
./install.sh
.venv-ui/bin/aconsole
```

Демо-режим без Podman и контейнеров: `.venv-ui/bin/aconsole --fake demo`.
Ядро отдельно: `.venv-core/bin/aconsoled --serve`.

## Первый запуск

1. Пройдите мастер настройки: он проверит `podman`, `distrobox`, контейнеры `agent-*`,
   версии CLI, vault, монтирование и `notify-send`.
2. Добавьте Git-проект — базовая ветка берётся из `HEAD`.
3. Создайте вкладку: новая или существующая сессия, агент, общий или изолированный режим.
4. Работайте: терминал, согласования, план, чекпоинты, Git и лимиты — в сайдбаре и на
   отдельных страницах.

## Документация

- [PROJECT.md](PROJECT.md) — полное описание проекта на версии 0.1.1: архитектура, слои,
  контракты, все 81 RPC-метод, события, ошибки, схема данных, установка и проверки.
- [ISSUES.md](ISSUES.md) — журнал проблем: что не работает в 0.1.1, что исправлено после
  выпуска и какие узлы плана остаются открытыми.
- [CHANGELOG.md](CHANGELOG.md) — что вошло в 0.1.0 и 0.1.1, что планируется в 0.1.2.
- [plans/0.1.1-git.md](plans/0.1.1-git.md) — план и контекст реализации 0.1.1.
- [artifacts/git-0.1.1/VERIFICATION.md](artifacts/git-0.1.1/VERIFICATION.md) — приёмка 0.1.1.
- [LICENSE](LICENSE) — условия распространения.

## Ограничения версии 0.1.1

- Прототип без обещания стабильного API; поддерживается только Linux.
- AConsole не управляет созданием контейнеров, монтированием и логином агентов. Если в
  контейнер смонтирован домашний каталог, база AConsole или файлы запуска хоста, агент может
  их изменить.
- У OpenCode и Cursor нет hooks: inline-согласований и автоматических чекпоинтов у них нет,
  статус определяется по экрану.
- Командная палитра, выбор существующей нативной сессии и продолжение диалога Cursor в этой
  сборке работают только на fake core: в настоящем ядре маршруты `palette/*` и `session/list`
  не обслуживаются.
- Запросы Write/Edit/MCP показывают не все детали; режимов «разрешать/запрещать» и уровней
  «глобально/проект» ещё нет.
- Подтверждения перед закрытием окна или вкладки с работающим агентом нет.
- Cursor и OpenCode отдают лимиты через непубличные endpoint'ы; формат может измениться.
- Пакет собран только для `amd64` и не подписан GPG.
- Согласования и allowlist — барьеры, а не песочница. Не давайте агенту доступ к тому,
  что не готовы потерять.

## Лицензия

MIT, см. [LICENSE](LICENSE). Copyright (c) 2026 secachiss-code.
