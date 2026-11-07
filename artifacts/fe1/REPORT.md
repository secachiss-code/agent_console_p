# FE-1 — устаревшие ответы и доставка в терминал

База: `ea0aa57b`; plan-1/node-51.

## Воспроизведение и исправления

До правок три управляемые UI-регрессии падали: исключение event subscriber
выходило из CoreStore.apply; старая страница Git дописывалась после смены
области; поздний список checkpoints заменял более новый. Настоящий временный
Daemon отдельно возвращал ok=True на комментарий незапущенному терминалу.

- FE-01: каждый event/snapshot/connection subscriber изолирован отдельным
  try/except Exception с журналированием. Остальные подписчики вызываются,
  sequence/model остаются применёнными; исключения транспорта не подавлены.
- FE-03: load_more сохраняет поколение, scope, version и cursor. Поздний
  результат или ошибка чужого контекста игнорируются. reload инвалидирует
  поколение синхронно, до старта очередной async refresh.
- FE-04: GitActions используют одну asyncio.Lock на панель; изменение Git
  не идёт параллельно другому действию этой панели. Ожидающий coroutine
  закрывается при отмене. Commit и файловые кнопки нечувствительны во время
  операции, внешний refresh не включает commit до её завершения.
- FE-05: checkpoints имеют отдельное поколение, включая ошибки загрузки.
  Старый ответ не очищает новый список.
- FE-06: PlanGraph.update обновляет состояние, ready/style и детали без
  замены кнопок и потери выбора/scroll/focus при неизменной геометрии.
  При изменении topology/key/title/layer используется существующее
  пересоздание. Heartbeat/priority/evidence не требуют замены виджета.
- FE-02: ReviewTarget сообщает started из TabService.running. Публичный
  diff/sendComment и общий send_text возвращают ok=False с понятным toast,
  если агент не запущен. Комментарий не считается отправленным; ReviewPage
  сохраняет существующий черновик при отказе. Запущенная вкладка сохраняет
  прежние немедленную доставку/очередь до Idle.
- Git inspector: поколения списка и файла разделены; очистка контекста,
  новое summary и сообщение об ошибке инвалидируют старый detail. Проверки
  source/path защищают от переключения контекста до старта новой загрузки.

Core-правки ReviewTarget/ReviewService и wiring MainWindow необходимы для
описанных критериев и отмечены в note узла. Wire-схемы не менялись.

Ссылки FE-10, FE-16, FE-17 в исходном objective не содержат описаний; ни
поиск checkout, ни ctx_get не нашли исходные находки. Они сохранены новым
узлом FE-2 для восстановления контекста. Перечисленные исправления не
выдаются за доказательство выполнения неизвестных сценариев этих ID.

## Проверки

Core в devtools, PYTHONPATH текущего worktree:

```
tests/core/test_review_delivery.py
tests/core/test_review_wire.py
tests/core/test_diff_review.py
tests/core/test_git_wire.py
tests/core/test_tabs.py
tests/core/test_tabs_restore.py
```

**52 passed**, 13.72 с. Новый public RPC тест подтверждает отказ до старта,
затем ok и InjectToTerminal после tab/started. Существующий queue-to-Idle
тест теперь явно отмечает запуск: раньше он объявлял вкладку Working hook-ом
при started=False. Его проверки доставки и отсутствия лишнего checkpoint
сохранены. Реальные пользовательские процессы не используются.

Native GTK в приватном Xvfb:

```
tests/gui/front/test_fe_races.py
tests/gui/front/test_review_page.py
tests/gui/front/test_git_pane.py
tests/gui/front/test_git_actions.py
tests/gui/front/test_b4a_plan.py
tests/gui/test_core_client.py
```

**51 passed**, 36.70 с, три прежних gi/Gtk deprecation warnings. Затем
добавлены регрессии snapshot/connection subscribers и clear inspector:
весь test_fe_races.py повторён, **6 passed**, 0.21 с. Эти шесть частично
пересекаются с 51; результаты не суммируются.

Strict basedpyright восьми изменённых модулей: 0 errors/0 warnings.
Ruff изменённых Python-файлов и git diff --check PASS.

## Surprises & Discoveries / визуальная проверка

Применён gtk-screenshot-review: просмотрены девять PNG из ui/ — review,
Git и plan-graph в светлой/тёмной теме1280×800 и светлой700×800. Нативный
сценарий — xvfb-runner.txt: отдельные Xauthority/config/state/cache, Xvfb
из временно извлечённого deb, без установки пакета и без рабочего дисплея.

- Первая защита busy отбрасывала следующий запрос. GUI-тест stage→discard
  выявил потерю действия; заменено последовательной очередью, весь набор
  повторён успешно.
- Host basedpyright wrapper содержал shebang /run/host/... и завершался127.
  Проверка выполнена core-basedpyright в devtools с UI venv/stubs.
- FakeCore Git в demo даёт пустую область истории; снимки подтверждают
  форму/темы, а заполненный Git проверен GUI-тестами на реальном временном
  репозитории. Эти снимки не объявлены эталоном содержимого истории.
- При700px граф намеренно шире viewport и прокручивается горизонтально;
  вкладка «Git и чекпоинты» использует штатный ellipsis. Нового clipping
  управляющих элементов не выявлено. Review показывает diff, кнопки и
  переведённые подписи; тёмные виды не имеют светлых участков.
- docs/design/DESIGN.md отсутствует; буквальное совпадение с ним не заявлено.
  Панель approvals на снимках — существующее поведение, её переделка APR-1
  не включена в FE-1.

Снимки отражают текущий layout; стили и layout не менялись. Не добавлялись
ack/retry протокол терминальных событий и сохранение черновика между
перезапусками: отказ устраняет конкретный сценарий отправки незапущенной
вкладке, а не гарантирует доставку через последующий обрыв IPC.
Установленное ядро, сервис hostrun и контейнеры пользователя не менялись.
