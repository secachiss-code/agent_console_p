# QA-30: sequential full/isolated scale with explicit production CSS

Base HEAD: `97cbf6c948ecdc0f61ee2f45c8b16db8a3a0fa7a`.

One full desktop UI run and one isolated original scrolling test were run,
in that order, without retries. Full: **527 passed, 9 failed**, 150.20 s.
Isolated: **1 passed**, 13.26 s. The nine full-suite failures are exactly the
QA28 failures: six narrow-window geometry/keyboard cases and three reviewed
visual-baseline cases. No new failures appeared after QA29's contrast-provider
cleanup. Both newly added CSS lifecycle tests passed, accounting for the two
additional passes relative to QA28. The complete test identities, failures,
warnings and tracebacks are retained in JUnit and raw logs.

| Original scale report | Full suite | Isolated |
| --- | --- | --- |
| Rows / measurement | 10,000 / 4 s | 10,000 / 4 s |
| Frames | 182 | 172 |
| Median / original limit | 16.9 / 17.5 ms | 17.1 / 17.5 ms |
| p95 / worst | 34.9 / 36.6 ms | 34.8 / 67.0 ms |
| RSS, start → end | 604 → 615 MiB | 198 → 260 MiB |
| Original assertions | PASS | PASS |

Both runs use `GskNglRenderer` on Intel Mesa ADL-N, X11 `:0`, with Mutter/Gala.
Neither renderer override nor desktop settings were changed. Both scale tests
request QA29's production CSS fixture. The scalar observation after the
original all-rows wait is identical in both runs:

| Widget | Allocation | CSS padding top/right/bottom/left |
| --- | --- | --- |
| Window / pane | 1280 × 800 | 0/0/0/0 |
| List / scroller | 620 × 754 | 0/0/0/0 |
| Toolbar content | 1248 × 34 | 6/16/6/16 |

The observer wraps the original pane callback and settling helper, delegates
every original call, and records once when the store reaches 10,000 rows.
It reads five widgets and scans only direct pane children to find the toolbar.
It adds no waits or frame callbacks and does not enumerate windows, tasks or
GLib sources. Scalar collection took 0.51 ms full / 0.70 ms isolated; those
numbers exclude JSON serialization and file I/O, which also occur before the
original measurement. The deprecated GTK padding accessor warning is retained.
The observation does not establish equivalence of every CSS property or the
entire process state.

`interval.json` brackets each pytest process including owned-process cleanup,
using wall and monotonic nanosecond clocks. The isolated process starts
100.575 s after the full process finishes; both clocks prove no overlap.
The full run retains higher RSS from preceding tests. This finite pair passes
the unchanged original performance assertions but does not establish general
reproducibility or explain QA26's earlier 31.4 ms failure. No retry-to-pass was
performed, and no further performance repetition is proposed here.

## Reproduction and verification

From the repository, with sandbox approval for the host UI interpreter:

```
distrobox-host-exec /home/sas/Projects/sp_aconsole_prototype/.venv-ui/bin/python artifacts/qa30/desktop.py --full
distrobox-host-exec /home/sas/Projects/sp_aconsole_prototype/.venv-ui/bin/python artifacts/qa30/desktop.py
python3 artifacts/qa30/verify.py
```

Run the first two commands sequentially. Each refuses to overwrite its output
directory and has a 300 s bound. Environment records include exact base HEAD,
empty product/test/tool patch, inherited display/renderer variables, GL and WM
identity; run records include exact pytest argv and exit code. The runner sets
X11 and GTK_A11Y=test, clears Wayland/Broadway, and uses the original scale-report
output. Production application identity is unchanged.

The verifier checks preserved source, tests, tools, QA26–29 evidence; exact
targets and native environment; original report structure; style/allocation
agreement; process ordering; and classifies failures against QA28. It hashes
all retained scripts, reports, raw logs, JUnit and metadata. Artifact scripts
pass Ruff E/F/I and compile. `git diff --check` flags only original pytest
whitespace in retained raw log/JUnit; that evidence is preserved verbatim. No product view/style/layout was edited, so no
new screenshot review is required. Reviewed PNG files remain unchanged.

QA-4 remains failed. Private geometry evidence remains separate from these
desktop results; CI, build/install and reproducibility acceptance are not
supplied by this pair. SEC-11/13, independent SEC34/GATE1 acceptance and Stage B
authority are unchanged. The next useful QA4 work is to inventory outstanding
CI/build/install evidence and choose a bounded verification task, rather than
repeat this GPU pair.
