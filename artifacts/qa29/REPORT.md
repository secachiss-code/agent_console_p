# QA-29: explicit production CSS for scale and owned provider cleanup

Base HEAD: `07e4f2b3b0d90a6ad68a0dc38fa518c5f9533d72`.

The native same-pane control establishes a CSS-dependent allocation difference:

| Scalar | Bare | Production stylesheet | Own provider removed |
| --- | --- | --- | --- |
| Window / pane | 1280 x 800 | 1280 x 800 | 1280 x 800 |
| List / scroller | 620 x 766 | 620 x 754 | 620 x 766 |
| Toolbar CSS padding (top/right/bottom/left) | 0/0/0/0 | 6/16/6/16 | 0/0/0/0 |

The stylesheet's `.ac-git-toolbar` adds six pixels above and below the toolbar.
This accounts for QA28's observed twelve-pixel list-height difference in this
controlled intervention, with the same live pane and window. It does not
establish a cause for QA26's earlier 31.4 ms frame median or reproduce all
resource/style state of the full suite.

Static registration paths:

- `src/aconsole/ui/views/app.py:Application.do_startup` loads `stylesheet()`
  into a display provider at APPLICATION priority. There is no symmetric removal
  there; production lifecycle is outside this node's scope.
- `tests/gui/front/test_git_pane.py:_on_pane` uses bare `Adw.Application`,
  without production startup or any stylesheet registration.
- Before this change `test_contrast_table` added both the production stylesheet
  and its theme probes to the default display and never removed either.
  Earlier full-suite Application tests can also install production CSS.
- `Window._apply_code_font` owns a separate APPLICATION+1 monospace provider.
  Nothing here removes or changes that provider or other owners' providers.

The scale module now explicitly requests `production_css`. The fixture uses
the exact production stylesheet and priority and removes only the provider it
created, including on exception. It is deliberately opt-in. The contrast test
uses the same fixture, removes its own probes in `finally`, and restores the
actual previous color scheme. Existing window-cleanup ownership is preserved.
Production source, original scale workload/functions/constants/assertions,
contrast assertions, reviewed images and desktop settings are unchanged.

Native results, with raw logs/JUnit retained:

| Run | Result | Seconds |
| --- | --- | --- |
| Same-pane bare/styled/removed | 1 passed | 5.52 |
| Initial focused | 15 passed, 2 failed | 39.67 |
| Corrected focused | 17 passed | 17.93 |
| Original scale module, explicit CSS | 2 passed | 13.71 |

The initial new lifecycle tests incorrectly used `Widget.get_opacity()` to
observe CSS opacity: that accessor did not report the CSS value. They now
observe a separate child's CSS padding from an unrelated provider. Lifecycle
checks run in fresh GTK processes so pre-existing Application CSS cannot mask
removal. Both normal and deliberate exception paths verify production toolbar
padding disappears and unrelated child padding survives. The initial edit also
accidentally replaced the front conftest's window-cleanup fixture; review caught
and restored it before the corrected run. The verifier checks its original AST.
The initial failed run and its tracked-source diff are retained without overwriting.

The scale report is the original test output: 10,000 rows, 174 frames / 4 s,
median 17.1 ms, p95 35.1 ms, worst 67.7 ms, RSS 260 MiB from 198 MiB.
The original assertions pass. However, JUnit intervals show 3.23 seconds of
overlap between the corrected focused process and the start of the scale
process. This is functional evidence only, not a controlled performance result
or a full/isolated equivalence claim. No repeat-to-pass was performed. The full
suite was not rerun in this node. A sequential full/isolated check remains next.

## Reproduction and verification

Use the existing host UI interpreter via `distrobox-host-exec` with sandbox
approval, from the repository. Output directories and `control.json` refuse
overwrite. Initial sandbox host invocation returned 127 without run data.

```
/home/sas/Projects/sp_aconsole_prototype/.venv-ui/bin/python tools/native_ui_tests.py --mode desktop --output artifacts/qa29/control --timeout 60 artifacts/qa29/test_css_control.py
/home/sas/Projects/sp_aconsole_prototype/.venv-ui/bin/python artifacts/qa29/run.py focused-fixed
/home/sas/Projects/sp_aconsole_prototype/.venv-ui/bin/python artifacts/qa29/run.py scale
python3 artifacts/qa29/verify.py
```

Run the commands sequentially for a fresh controlled reproduction. The runner
records HEAD, tracked test diff, inherited relevant environment, exact pytest
command, backend and exit status; sets X11/GTK_A11Y=test, clears Wayland/Broadway
and supplies the original scale-report path without renderer override.
The control collects scalar metrics after bounded 0.4 s native settling per
phase; it does not scroll or measure frame performance. Its initial PyGI Gdk
version warning is preserved. After execution only import ordering and string
parentheses were adjusted for lint, with no runtime change.

The verifier checks original scale function/constant ASTs, unchanged contrast
assertions and window-cleanup fixture, preserved source/tools/baselines/QA26-28,
native outcomes, actual scalars and process overlap; it hashes retained evidence.
Changed test files pass full Ruff; artifact scripts pass Ruff E/F/I and compile.
No product view/style/layout changes were made, so no new screenshot review is
required.

QA-4 remains failed. Earlier private geometry evidence stays separate; CI,
build/install and reproducibility acceptance remain outstanding. SEC-11/13,
independent SEC34/GATE1 review and Stage B authority are unchanged.
