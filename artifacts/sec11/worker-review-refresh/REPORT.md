# SEC-35: SEC34 provenance reconciliation after QA4

Reviewed source HEAD: `b4222eed15ffbc87bb1c0ace7a60008f260301a3`.
SEC34 historical base: `fea8c49c3604bde3e5fdb8fd2e2fa263970aec03`.
Disposition: **historical packet intact, current-source verification FAIL**.
Independent review remains **NOT PERFORMED**; Stage B authorization is absent;
The saved technical SEC11/13 verdicts remain failed. QA4's user acceptance is preserved.

User subsequently instructed: «проверяй и поясни в чем проблема. Так же sec 11,
sec 13 и sec 35 этого прими.» SEC11, SEC13 and SEC35 are therefore accepted by
the user for plan closure, with the reproduced limitations explained. This is a
user disposition, not a new passing attack result or independent installed verdict.
The `SEC11`/`SEC13` values in `results.json` preserve technical verdicts from the
original evidence; they are not current plan-status fields. No Stage B implementation
or installation follows solely from closing these three nodes.

## Actual result

The unchanged command `python3 -B artifacts/sec11/worker-review-gate/verify.py`
exits 1 at `Source/evidence index drift`. Exact stdout and stderr are retained
in `baseline.stdout` and `baseline.stderr`; only the workspace root in traceback
filenames is normalized to `<ROOT>`. No original files or recorded hashes were
changed. `results.json` records HEAD, hashes, exit and the complete changed-path
list. `product.patch` is the complete source diff from the historical base.

Every one of the 279 historical product Python hashes and 52 upstream evidence
hashes still matches its original Git base blob. All 52 upstream files, including
the original probes and SEC30–33 artifacts, also match current filesystem bytes.
Four product Python files differ now. No core file differs. One CSS change is
outside SEC34's Python-only source inventory and is therefore recorded separately.

| File | Reviewed change | Implication for worker review |
| --- | --- | --- |
| `src/aconsole/ui/bootstrap.py` | Close unowned coroutine on scheduling failure; Runner owns shutdown | UI lifecycle changed; no Git worker implementation or confinement added |
| `src/aconsole/ui/views/review/git_pane.py` | One-column GridView replaces ListView | Git display geometry changed; no command or worker authority change |
| `src/aconsole/ui/views/setup/preferences.py` | Delay confirmed close until opening ticks finish; cancel callback on unmap | Settings dialog lifecycle changed; no authenticated worker request or approval protocol added |
| `src/aconsole/ui/views/window.py` | Startup focus grace for approval notifications; timer/disposal cleanup | Approval alert timing changed; does not establish independent review or one-shot hook grants |
| `src/aconsole/ui/style/style.css` | Rendering selectors/layout for Git rows | Additional non-Python provenance change; no execution boundary implemented |

These are static diff observations. They do not prove absence of all security
effects in UI behavior. Unchanged core/probe bytes preserve historical provenance,
but do not establish current installed acceptance or turn the original failing
security probes into passing tests. The historical SEC34 verifier's failure is
expected from its exact-source contract; no weaker assertion replaces it.

## Concrete next decision

Submit SEC34's exact `contract.json`, its historical source index and this delta
to an independent reviewer. Review must explicitly bind the chosen source revision
and decide whether the five UI/CSS changes require further evidence. Keep the old
packet intact; any accepted current-source packet must be a separately versioned
review artifact with its own provenance, not overwritten historical validation.

The reviewer must resolve three existing implementation blockers before Stage B:

1. Accept or reject the narrow two-operation v1 scope, fixed status/index
   observations and proposed resource limits while full Stage A stays incomplete.
2. Select and review a concrete native launch/exec-denial mechanism, protected
   runtime, exact index/object write boundary, hard resource enforcement and durable
   quarantine. SEC34 currently lists requirements, not an implemented mechanism.
3. Approve a concrete installed probe adapter that preserves original SEC11/13
   schedules and assertions, including native attack controls. The original SEC13
   launcher alone cannot test a new installed worker.

After a recorded review disposition, create a separate implementation node.
Protected installation and independent B01–B19 acceptance follow implementation;
product routing remains a later stage. General plan continuation and this local
integrity check do not supply an independent security verdict. There is no new
open-ended census task and no reason to repeat accepted QA4/GPU/coverage runs.

## Verification and limits

`python3 -B artifacts/sec11/worker-review-refresh/verify.py` reproduces the original
red verification, verifies historical base blobs, current file identities, exact
product diff, saved output and disposition. It tolerates only descendant HEADs
whose product/evidence bytes still match. Its PASS means reconciliation integrity.
It cannot authenticate its own agent-writable evidence or approve Stage B.

No product edits, attack executions, installation, host policy, dependencies,
permissions, runtime routing or security-status promotion occurred. No new full
suite, CI, coverage, UI or installed security PASS is claimed.
