# QA-32: fresh local branch coverage, retention criterion failed

Frozen product HEAD: `4c13526393a14325f4e78adfbcde392d8312db3f`.
One sequential pair, no retries: core **2181 passed, 1 skipped, 1 deselected**
(470.82s); owned private-Xvfb UI **535 passed, 1 deselected** (161.47s).
Both pytest exits 0. Workflow lists and original single scrolling deselection
were retained; core used a basetemp longer than 100 characters. No product,
tests, workflow, thresholds, reviewed images or desktop settings changed.

Actual combine and report exited 0. Combined branch coverage is
**90.08038451976465%** (CLI 90.08%), with unchanged source `src/aconsole`,
branch=true and fail_under=90. This refreshes local coverage only. Remote CI,
deb installation, security, QA4 and Stage B acceptance are not promoted.

## Retention failure

QA32 acceptance is **FAILED**, despite passing tests and coverage: separate
core/UI datasets were lost. `combine --keep` preserved them initially, but
installed coverage 7.17.0 `CoverageScript.command_line` calls
`self.coverage.combine(strict=False, keep=bool(options.keep_combined))` for
report actions too. The original report lacked `--keep-combined`; it discovered
`.coverage.core` and `.coverage.ui` beside `.coverage` and removed those inputs.
The remaining merged database cannot reconstruct their separate provenance.
No copies are fabricated. `coverage-runtime.json` preserves the installed
implementation and API-derived exact percentage; `api-report.txt` reports
without invoking CLI auto-combine. The API loads the retained combined database.

## Evidence and reproduction

`executed-run.txt` is the exact executed runner, hashed in `inputs.json`.
`run.py` is its subsequently formatted/annotated version with the same commands.
`results.json` contains commands, HEAD, scalar environment, start/end wall-clock
nanoseconds, exits and hashes of every Python/CSS input under src/tests/tools.
The core interval ends before UI starts; UI ends before combine starts.
Sources remained identical at the end and are rechecked by `verify.py`.
JUnit and complete raw logs preserve both suites, warnings and exclusions.
`.coverage` is the actual merged SQLite database; its hash is in `evidence.json`.

Host Xvfb was downloaded using `apt-get download xvfb`, Ubuntu noble-updates
2:21.1.12-1ubuntu1.9, then extracted with dpkg-deb; nothing was installed.
Package hash is in `inputs.json`; the untracked downloaded/extracted binary
is excluded from Git. The existing private_display owns server/auth and cleanup.
Its log is retained in display/xvfb.log. Both test groups were bounded at 1200s
with process-group cleanup. Initial host probes found the old /tmp Xvfb absent;
no tests ran during those probes. No GPU scrolling pair was repeated.

Run `python3 artifacts/qa32/verify.py` to validate the retained result, including
its explicit missing-input failure, without rerunning tests. Its successful exit
means evidence is consistent, not that the QA32 retention criterion passed.

## Next action

A separate bounded evidence task should preserve each completed dataset under
an archive directory outside the combined data-file prefix **before** combine,
hash it immediately and use `coverage report --keep-combined --precision=2`.
Then execute one sequential pair on a frozen source, regardless of test exits,
and verify both archived databases and the merged result. This repetition is
justified solely by the measured retention failure, not by seeking a passing
coverage score. Do not reuse the combined QA32 database as either fresh input.
No product coverage repair is indicated by the observed 90.08% result.
