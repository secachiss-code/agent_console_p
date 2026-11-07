# UX-1 — приёмка горячих клавиш

База: `fd865c88`. Узел `plan-1/node-39`.
Принято существующее исправление `09ac8ffa7ce1eb182ceea92d21e6507099d562d8`.

## Проверенное поведение

- Все действия DEFAULT_BINDINGS имеют обработчик MainWindow и название
  в справке. Обратная проверка также не находит обработчиков без keymap.
- Неизвестное/необслуженное действие возвращает False; capture callback
  передаёт фактический результат dispatch вместо безусловного True.
  Добавлены две регрессии с настоящим Gtk.CallbackAction.activate.
- Терминальные copy/paste выполняются только при фокусе терминала; остальные
  editable widgets сохраняют свои клавиши. Ctrl+C без выделения остаётся
  прерыванием. Ctrl+V и Shift+Insert также проверены.
- Реальные XTest-сочетания проходят через CAPTURE MainWindow к VTE и GDK
  clipboard: Unicode/многострочный текст, image, mixed, empty и отмена при
  смене фокуса. Проверены bracketed paste и ввод в Gtk.Entry.
- Добавлен реальный Ctrl+Shift+C: выделение VTE попадает в GDK clipboard,
  sentinel прочитан обратно через GDK API, процессу PTY не передан Ctrl+C.
- Поиск VTE проверен до realize, на буквальном тексте, без совпадений и при
  повторном поиске. Старый override удалённого handover не ломает остальные
  bindings. README не обещает отсутствующий handover shortcut; одноимённая
  строка в README описывает отдельный MCP-инструмент.

## Проверки и окружение

Финальный набор текущего worktree на отдельном Xvfb:

```
tests/gui/front/test_ux1_shortcuts.py
tests/gui/front/test_clipboard_routing.py
tests/gui/front/test_terminal_clipboard.py
tests/ui/test_b1_keymap.py
```

**28 passed**, 7.96 секунды, exit 0. Ruff обоих изменённых файлов и
`git diff --check` проходят. Восемь deprecation warnings gi.events/VTE и
предупреждения xkbcomp о дополнительных XF86 keysyms не вызвали ошибок.

Первый Broadway-прогон дал 20 passed / 5 failed: XTest не смог открыть X11
display. Это не заменено пропусками: после перехода на отдельный Xvfb все
пять end-to-end сценариев прошли. Затем добавлены две callback-регрессии и
одна настоящая copy-регрессия; итоговый прогон включает их все.

Xvfb скачан с разрешения пользователя из настроенного Ubuntu apt repository:
`2:21.1.12-1ubuntu1.9`, 877 kB. Пакет только распакован в приватный временный
каталог, системная установка не выполнялась. Сервер имеет собственный
Xauthority cookie и `-nolisten tcp`; DISPLAY пользователя не используется.
После проверки сервер остановлен, временные файлы удалены. Воспроизводимый
runner сохранён в `xvfb-runner.txt` (запуск UI Python на хосте).

Production views/styles/handlers не менялись. Проверка установленного живого
окна остаётся в AUD-2, как предусмотрено исходной границей UX-1; здесь
проверены настоящие GTK/VTE/clipboard/key events с fake core на личном X11.
