# APR-1: карточка поверх терминала и доступность

База: `503e3de771d903dc08915ba09e933bbc7eb0f214`.
**Технические проверки выполнены; пользовательская визуальная приёмка ожидается.**
Узел не отмечен done. Принятие снимков пользователем — прямое условие APR-1.

## Что уже было реализовано

Brief ссылается на старый add_bottom_bar. На текущей базе overlay уже реализован
коммитом `14666d84` и интегрирован в main. Повторная реализация не нужна:

- window.py: `_install_approvals` использует Gtk.Overlay; карточка не участвует в измерении терминала.
- panel.py: детали в ScrolledWindow с ограничением высоты, кнопки вне него; кнопки переносятся в узком окне.
- risky Allow ждёт 500 ms, не suggested/default; защита смены карточки 600 ms и busy guard сохранены.
- Контекст клавиш ограничен фокусом карточки; запросы другой вкладки считаются отдельно.
- style.css: появление 200 ms; системное отключение анимаций соблюдается существующим CSS.

Перед изменением кода пользователю показаны три снимка текущего вида и задан
вопрос о приёмке. Это выполняет требование brief «Перед кодом показать макет
пользователю». Ответ к моменту составления отчёта не получен; согласие не предполагается.

## Найденный и исправленный дефект

Фокус действительно достигал деталей (`DETAILS_FOCUSED True`), но внешнее дерево
AT-SPI возвращало для focusable scroll pane пустые name и description.
Orca произносил только `scroll pane`, хотя Gtk accessible properties задавались.

Единственное продуктовое изменение: явный `accessible_role=Gtk.AccessibleRole.GROUP`
для контейнера деталей. Внешнее дерево сохраняет роль scroll pane, но теперь
экспортирует полный заголовок, риск, summary и описание cwd. Orca передал в speech
output строку «Claude хочет выполнить команду. Удаляет данные. rm -rf build scroll pane.».
Это подтверждение генерации речевого вывода, а не ручная оценка звука из динамиков.

Изменения обновления properties при map и передачи обычных Python strings
не помогли и удалены. Их диагностические tree/debug-файлы имеют суффикс rejected.
Начальная попытка истекла за 30 s во время запуска служб private session;
она не засчитана как accessibility PASS. Повтор ждёт реальной готовности карточки.

## Проверки

- Исходный набор approvals panel/alerts/viewmodel: 32 passed; финальный повтор хранится
  в final-tests.xml. Прохождение набора само по себе не доказывает чтение Orca.
- `check-a11y.txt` проверяет внешние name/risk/command/cwd, все три кнопки и speech output.
  Он FAIL на сохранённом исходном atspi-tree-baseline.json и PASS на финальном дереве.
- Настоящий Orca запускался на отдельном D-Bus и Xvfb с временными HOME/XDG и preferences.
  Настройки пользовательского screen reader не менялись. Хранятся полные debug и tree.
- Ruff и strict basedpyright с UI environment хоста проверены; контейнерная попытка
  не нашла .venv-ui/gi и не засчитана. Map-прототип имел ошибки callback typing и удалён.

## Снимки и Surprises & Discoveries

Применён gtk-screenshot-review. Частный Xvfb используется как established fallback:
hostrun snapshot обращается к общему main, а проверяется текущий worktree.
Фикстура — FakeCore demo из artifacts/des4/capture.txt, не реальные запросы агента.
Изображения ui/ru-approvals-{light,dark,narrow}.png:1280x800/light,dark и700x800/light.
Все открыты для проверки. Карточка внизу поверх терминала; кнопки видны, ключей
перевода и обрезания карточки нет. В dark нет светлых участков карточки.
Сверены DESIGN §1 и §2.1 в общем checkout. Виджет «Сессия завершена» в фоне
части кадров — состояние демонстрационной терминальной фикстуры, не ошибка карточки.

Визуальная проверка автора не заменяет требуемое принятие пользователем.
Исходники/хеши и база в source.json. Итоговая приёмка APR-1 остаётся открытой.

## Воспроизведение

На хосте с существующим UI environment:

```sh
distrobox-host-exec /home/sas/Projects/sp_aconsole_prototype/.venv-ui/bin/python artifacts/apr1/runner.txt
distrobox-host-exec /usr/bin/dbus-run-session -- /home/sas/Projects/sp_aconsole_prototype/.venv-ui/bin/python artifacts/apr1/a11y-runner.txt
python3 artifacts/apr1/check-a11y.txt
python3 artifacts/apr1/check-a11y.txt --tree artifacts/apr1/atspi-tree-baseline.json --speech artifacts/apr1/orca-debug-baseline.txt
```

Последняя команда должна отказать: это отрицательный контроль регрессионной проверки.
Скрипты извлекают Xvfb во временную папку; пакеты не устанавливаются. Для distrobox-host-exec
использовать абсолютные пути к script/current worktree, если cwd хоста отличается.
Изолированный Orca использует только тестовые запросы; обход всего пользовательского desktop не выполнялся.
