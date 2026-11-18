# IDX-4 — опубликованный C# DLL и проба Native AOT

2026-11-18. Узел `node-905`, plan-900, attempt 1.
Реализация готова к проверке; узел ещё не принят.

## Выпущенный путь

Framework-dependent `csgraph.dll`, .NET SDK 10.0.112, runtime 10.0.12,
Fedora 44 x64, devtools. Roslyn берётся из установленного SDK через
`$(MSBuildSDKsPath)/../Roslyn/bincore`; PackageReference нет.
Офлайн-публикация выполнена успешно, DLL и зависимости установлены:

```sh
dotnet publish tools/csgraph/csgraph.csproj -c Release -p:NuGetAudit=false \
  -o /home/sas/.containers/containers/devtools/.local/lib/aconsole-csgraph
```

Боевой argv hostrun:

```text
dotnet exec /home/sas/.containers/containers/devtools/.local/lib/aconsole-csgraph/csgraph.dll <relative-input>
```

В нём нет run/build/restore/publish. Индексация не выполняет MSBuild,
проектные задачи, анализаторы, генераторы или код решения. .csproj/.sln/.slnx
читаются как данные; ProjectReference связывает Roslyn compilations без Emit.
Граф v1 содержит модули `cs:`, прямые inherits/implements и сигнатуры без тел.
Ошибки синтаксиса дают пустой интерфейс и снимают исходящие рёбра файла.

Ограничения: поддерживается стандартный набор исходников SDK-проекта;
Directory.Build.props/targets, зависимости NuGet и произвольная оценка MSBuild
не выполняются. Явные Compile/Import/DefineConstants, условные ProjectReference
и отключённые default compile items отвергаются. Внешний неразрешённый тип
не создаёт ребро. Детали и команды — `tools/csgraph/README.md`.

## Проба AOT

Исходник использует только Microsoft.CodeAnalysis и Microsoft.CodeAnalysis.CSharp;
Workspaces, Scripting, Emit и загрузки анализаторов нет. Выполнено в devtools,
без сетевых источников (RestoreSources указывает на локальный empty-feed):

```sh
dotnet publish tools/csgraph/csgraph.csproj -c Release -r linux-x64 \
  -p:PublishAot=true -p:NuGetAudit=false -o tools/csgraph/aot-probe
```

Результат: exit 1 на restore, NU1101. В локальных источниках empty-feed и
/usr/lib64/dotnet/library-packs отсутствуют:

- Microsoft.NETCore.App.Runtime.linux-x64;
- Microsoft.AspNetCore.App.Runtime.linux-x64;
- Microsoft.NETCore.App.Runtime.NativeAOT.linux-x64;
- runtime.linux-x64.Microsoft.DotNet.ILCompiler.

До AOT-компиляции и проверки trimming Roslyn проба не дошла. Совместимость
Native AOT не доказана; SDK или пакеты из сети не устанавливались. После пробы
обычная DLL-публикация повторена успешно. По §2.4/IDX-4 плана провал пробы
не препятствует DLL-выпуску.

## Проверки и оставшееся препятствие

Все команды выполнялись в devtools по точному текущему worktree:

```sh
.venv-core/bin/python -m pytest tests/hostrun/test_csharp_info.py -q
.venv-core/bin/python -m ruff check tests/hostrun/test_csharp_info.py tools/hostrun/actions.py
.venv-core/bin/python -m basedpyright tests/hostrun/test_csharp_info.py tools/hostrun/actions.py
.venv-core/bin/python -m pytest tests/hostrun -q
```

21 проверка C# прошла, включая семантику двух проектов, внешние неизвестные
типы, повторный запуск DLL после удаления bin/obj, отсутствие исполнения
проектных Build targets, стабильность символов при замене тела, смену
сигнатуры, синтаксическую ошибку, циклы ProjectReference и запрет symlink.
Линтер и строгая типизация прошли (0 errors, 0 warnings, 0 notes).

Полный hostrun-прогон выявил единственный упавший тест:
`tests/hostrun/test_server.py::test_devtools_extractor_argv[csharp-src/Cs-tail3]`.
Он дословно требует старый `dotnet run --project tools/csgraph -- src/Cs`.
Это противоречит целевому argv IDX-4. Остальные тесты прошли, один пропущен.

Файл test_server.py уже есть в scope родителя IDX-0, но отсутствует в
замороженном scope IDX-4 (contract
5823d350bd9926ce4228f5c471428bda62a063d7f88db369c97c7d0807bc8f49).
Файл не изменён. Точная предлагаемая замена одного параметра теста лежит
в `tools/csgraph/scope-repair.patch`; проверки и ограничения не снимаются.
Нужен штатный repair_paths для IDX-4 с добавлением test_server.py, затем
применение этой правки и свежие Micro/Blast на новом контракте.
Автоматический переход в done и обход review_required не выполнялись.
Hostrun-служба основного checkout не переключалась на незавершённое дерево.

## Штатные квитанции попытки 1

L2 spec → patch → test → verify завершён. Micro прошёл:
`40b971c4dff07193b595d65617c60a79a6747d2cf45dcdb98417dcd2faadfcf1`.
Blast: **1 passed, 1 failed**, receipt
`9ea53bcfd14ef3c16d6e8b468e4c03f90cb3623272ca1095466edfe9cb0c47b6`.
Команда узла зелёная; зависимые тесты падают на указанном старом argv.
Квитанции относятся к состоянию до добавления этого раздела отчёта;
после ремонта контракта нужны свежая попытка и новые квитанции.
