# CORE-4 — MCP leases, bounds and tab isolation

Base: ce759aa on shared main. Product changes are confined to the core.

## Acceptance and implementation

1. Lease IDs issued by a board include the plan ID: `<plan_id>:<key>:<fence>`. Newly created and restored DAGs use the same namespace. Existing persisted IDs are retained until a fresh claim. A live daemon test claims identical keys in two persisted plans, reloads them, heartbeats both, and reads them back from SQLite.
2. MCP arguments have explicit string and list limits, including nested node paths, acceptance, dependencies, knowledge references and handover lists. At most 100 nodes enter one create/add call. Authenticated and public POST bodies are buffered only up to 1 MiB before JSON parsing; both Content-Length and chunked requests are checked. Successful tool JSON is capped at 256 KiB of UTF-8. A larger answer returns RESPONSE_TOO_LARGE with a concise/revision/narrow-query hint rather than silently dropping nodes. Transport session ownership cache is capped at 2048 entries; an evicted session must initialize again and cannot bypass authentication.
3. A real HTTP test opens two tabs of one project through IPC, obtains their generated launch configurations, and calls the daemon using each bearer. Foreign heartbeat/status changes and artifact attachment are denied even with the correct fence. Each tab reads only its handover. Reusing another tab's MCP session ID with either a foreign bearer or no bearer receives HTTP 403; the owner's session still works. Existing live cross-project isolation is also covered.
4. Detailed plan_get includes depends_on keys. The revision/delta behavior is preserved and tested.

The same investigation fixed node_attach's missing owner check. MCP HTTP sessions are pinned to the tab that initialized them, before transport dispatch. Neither a fence nor a session ID alone authorizes a different tab.

The brief also identified false kb_add success: a configured knowledge database previously logged and discarded a fact whose project row was missing. It now raises a write error before the board changes memory. Existing persistence, restart and failed-write tests remain unchanged and pass. Explicit in-memory boards continue to support isolated tests.

## Validation

- Tests were written before implementation. The baseline reproduced SQLite UNIQUE constraint failure for leases.lease_id, missing dependencies, oversized inputs/output accepted, false KB acknowledgement, and foreign lease attachment. The first new HTTP fixture lacked an initial Git commit; only that new fixture was corrected before relying on its result.
- Final targeted command: `hostrun test core tests/core/test_mcp_core4.py tests/core/test_mcp_core4_http.py tests/core/test_mcp_core4_knowledge.py tests/core/test_mcp_tools.py tests/core/test_mcp_retry.py tests/core/test_mcp_auth.py tests/core/test_plan_store.py tests/core/test_knowledge_store.py tests/core/test_claude_mcp.py`: exit 0, 56 tests.
- `hostrun test core`: exit 0, 1794 cases; raw output in full.txt.
- `hostrun check`: exit 0. Ruff, strict basedpyright (0 errors/warnings), six architecture contracts, ten contract checks, contrast and API floor pass. The known ownership step prints “Not a git repository”; ownership is not claimed as verified.
- `git diff --check`: exit 0.

New ASGI boundary typing escapes follow the existing raw-header convention: `_session_header` ignores unknown list element/argument types only after `_header_pair` validates bytes. No domain typing escape or public application contract was added. Existing tests were not edited, skipped or weakened.

The legacy phase gate does not accept CORE task IDs, so no legacy gate PASS is claimed. Live integration here means isolated test daemons with real HTTP, IPC and SQLite; the installed desktop package remains the separately verified ecac0a6 build until a subsequent application update.
