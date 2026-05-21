# Migrate aery_Qgis → aerforge: Python Agent Incremental Migration

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Take the working v0.7.2 `aery_Qgis` plugin and incrementally migrate it toward the aerforge v1.1.2 Python-agent architecture, ship a working QGIS plugin at every step.

**Architecture:**
- Baseline: `aery_Qgis` (v0.7.2, Node.js RPCBridge, 111/111 tests pass, installed in QGIS)
- Target: `aerforge` Python agent files (v1.1.2: `agent.py`, `llm_client.py`, `tools.py`, `session.py`, plus new UI modules)
- Approach: Work in the `aerforge` repo. Verify each step with both unit tests and a QGIS installation. Roll back = uninstall + re-run.

**Current state of aerforge:**
- ✅ `llm_client.py` — OpenAI/Anthropic/Google/Groq/Ollama direct Python HTTP client (1112 lines)
- ✅ `tools.py` — 20+ geospatial tools, Graph toolchain, permission system (795 lines)
- ✅ `agent.py` — async agent loop with tool calling, failover, retry, session mgmt (657 lines)
- ✅ `session.py` — disk-backed session persistence
- ✅ `geospatial_tools.py` — 16 QGIS-native geospatial wrappers
- ⚠️ `chat_panel.py` — rewritten for `agent` but `test_improvements.py` has 7 FAIL (mock→async iterator issues)
- ⚠️ `plugin.py` — references `Agent` but uses monkey-patching `agent._on_worker_finished = ...` to wire callbacks
- ✅ `oauth_helper.py`, `qgis_executor.py` — same as aery_Qgis (working)
- ✅ 202/202 aerforge unit tests pass
- ⚠️ **Uncommitted work** (dirty repo, 6 files unstaged on main)
- ❌ **Never installed in QGIS** — unverified for QGIS runtime

---

## Chunk 1: Baseline and Install

### Task 1.1: Make aerforge HEAD self-documenting and install it

- [ ] **Step 1: Commit aerforge HEAD as baseline**
  ```bash
  cd /home/aryee/Desktop/aerforge/aery-qgis-plugin
  git add -A && git commit -m "baseline: aerforge v1.1.2 pre-migration (202 tests pass)"
  ```

- [ ] **Step 2: Run aerforge test suite — record baseline numbers**
  ```bash
  cd /home/aryee/Desktop/aerforge/aery-qgis-plugin && python3 -m pytest tests/ -q --tb=line 2>&1 | tail -5
  ```
  Record: `___ passed, ___ failed`

- [ ] **Step 3: Run `install.sh`**
  ```bash
  bash /home/aryee/Desktop/aerforge/aery-qgis-plugin/install.sh
  ```

- [ ] **Step 4: Verify the symlink**
  ```bash
  python3 -c "import os; p='/home/aryee/.local/share/QGIS/QGIS4/profiles/default/python/plugins/aery_plugin'; print('symlink:', os.path.islink(p), '→', os.readlink(p) if os.path.islink(p) else 'N/A')"
  ```
  Expected: `symlink: True → /home/aryee/Desktop/aerforge/aery-qgis-plugin/aery_plugin`

- [ ] **Step 5: Commit**
  ```bash
  git add -A && git commit -m "install: aerforge symlinked to QGIS plugins dir"
  ```

---

## Chunk 2: Audit and copy stable bug fixes from aery_Qgis

### Task 2.1: Diff key shared files — decide which version is better

Run these diffs and write results to the plan:

```bash
# rpc_bridge.py
diff -u /home/aryee/Desktop/aery_Qgis/aery-qgis-plugin/aery_plugin/rpc_bridge.py \
       /home/aryee/Desktop/aerforge/aery-qgis-plugin/aery_plugin/rpc_bridge.py | head -80

# qgis_executor.py
diff -u /home/aryee/Desktop/aery_Qgis/aery-qgis-plugin/aery_plugin/qgis_executor.py \
       /home/aryee/Desktop/aerforge/aery-qgis-plugin/aery_plugin/qgis_executor.py | head -40

# oauth_helper.py
diff -u /home/aryee/Desktop/aery_Qgis/aery-qgis-plugin/aery_plugin/oauth_helper.py \
       /home/aryee/Desktop/aerforge/aery-qgis-plugin/aery_plugin/oauth_helper.py | head -40

# smoke_test.py
diff -u /home/aryee/Desktop/aery_Qgis/aery-qgis-plugin/smoke_test.py \
       /home/aryee/Desktop/aerforge/aery-qgis-plugin/smoke_test.py

# uninstall.sh
diff -u /home/aryee/Desktop/aery_Qgis/aery-qgis-plugin/uninstall.sh \
       /home/aryee/Desktop/aerforge/aery-qgis-plugin/uninstall.sh
```

Record decision per file:
- **[AERY-QGIS BETTER]** — copy from aery_Qgis to aerforge
- **[AERFORGE BETTER]** — keep aerforge as-is
- **[IDENTICAL/NEUTRAL]** — no action

### Task 2.2: Port aery_Qgis bug fixes

Carry across only the fixes where aery_Qgis is better.

**Fix 1: `_poll_dead` mock safety** — only if aerforge is missing `isinstance(rc, int)` guard.

**Fix 2: `_read_stdout` "\n" loop** — aerforge should `continue` on `"\n"`, not `break`.

**Fix 3: `_read_stderr` duplicate** — check with `grep -n "def _read_stderr" aerforge/aery_plugin/rpc_bridge.py`; if it appears twice, remove the second one.

**Fix 4: `uninstall.sh` `set -e`**

**Fix 5: `smoke_test.py` import fix** — if aerforge has same broken import pattern as aery_Qgis did.

- [ ] For each fix:
  - [ ] Implement the change in aerforge
  - [ ] Run `python3 -m pytest tests/ -q` — tests must stay green
  - [ ] Commit

---

## Chunk 3: Add Qt signal adapter to `agent.py`

**Root problem:** aerforge's `chat_panel.py` connects to the agent via method-assignment monkey-patching:
```python
self.agent._on_worker_finished = self._on_agent_response   # fragile
self.agent._on_worker_error   = self._on_agent_error
```
Replace with proper Qt signals on `_AgentWorker`.

### Task 3.1: Add `permission_request` signal to `_AgentWorker`

**Files:** `aerforge/aery_plugin/agent.py`

- [ ] **Step 1: Write failing test** (`tests/test_agent.py`)
  ```python
  def test_permission_request_signal_emitted(qtbot):
      """_AgentWorker emits permission_request when tool requires permission."""
      from aery_plugin.agent import _AgentWorker, Agent
      from unittest.mock import MagicMock

      worker = _AgentWorker.__new__(_AgentWorker)
      worker._agent = MagicMock()
      worker._agent._permission_needed = False

      captured = []
      def handler(d):
          captured.append(d)
      worker.permission_request.connect(handler)

      # Simulate the permission path in run()
      worker.permission_request.emit({"type": "permission_request", "tool_name": "run_qgis_code"})

      assert len(captured) == 1
      assert captured[0]["tool_name"] == "run_qgis_code"
  ```

- [ ] **Step 2: Run test and confirm it FAILS** (signal doesn't exist yet)
  ```bash
  python3 -m pytest tests/test_agent.py::test_permission_request_signal_emitted -v 2>&1 | grep -E "FAIL|ERROR|permission_request"
  ```

- [ ] **Step 3: Add signal to `_AgentWorker` in `agent.py`**
  In `_AgentWorker.__init__` (line ~38), existing signals:
  ```python
  finished = pyqtSignal(str)
  error   = pyqtSignal(str)
  chunk   = pyqtSignal(dict)
  ```
  Add: `permission_request = pyqtSignal(dict)`

- [ ] **Step 4: Emit signal from permission branch in `run()`**
  In `agent.py` `run()` at the `self._permission_needed = True` block (~line 477):
  ```python
  if on_event:
      on_event({...permission_request dict...})
  # ALSO:
  self.permission_request.emit({...same dict...})
  ```

- [ ] **Step 5: Run test, expect PASS**
  ```bash
  python3 -m pytest tests/test_agent.py::test_permission_request_signal_emitted -v
  ```

- [ ] **Step 6: Run full aerforge suite**
  ```bash
  python3 -m pytest tests/ -q --tb=line
  ```
  Expected: ≥ 202 pass (same as baseline or more)

- [ ] **Step 7: Commit**
  ```bash
  git commit -am "feat(agent): add permission_request Qt signal to _AgentWorker"
  ```

---

## Chunk 4: Clean up `chat_panel.py` — remove all RPCBridge references

### Task 4.1: Purge RPCBridge API from aerforge `chat_panel.py`

- [ ] **Step 1: Document every RPCBridge reference in aerforge `chat_panel.py`**
  ```bash
  grep -n "rpc\|RPCBridge\|disconnect_rpc\|set_rpc\|rpc_bridge\|rpc\.\|\.rpc" \
    /home/aryee/Desktop/aerforge/aery-qgis-plugin/aery_plugin/chat_panel.py | grep -v "def _rpc\|#.*rpc"
  ```
  Should be **zero results**. If any remain, they are refactoring debt.

- [ ] **Step 2: Verify `_dispatch_prompt` uses `agent.start()`**
  ```bash
  grep -A5 "def _dispatch_prompt" /home/aryee/Desktop/aerforge/aery-qgis-plugin/aery_plugin/chat_panel.py | head -10
  ```
  Expected: `self.agent.start(text)` — no `self.rpc.prompt()`.

- [ ] **Step 3: Verify `_abort` uses `agent.cancel()`**
  ```bash
  grep -A3 "def _abort\|agent.cancel" /home/aryee/Desktop/aerforge/aery-qgis-plugin/aery_plugin/chat_panel.py | head -10
  ```
  Expected: `self.agent.cancel()` — no `self.rpc.abort()`.

- [ ] **Step 4: Run tests**
  ```bash
  python3 -m pytest tests/ -q --tb=line
  ```

- [ ] **Step 5: Commit**
  ```bash
  git commit -am "refactor(chat_panel): remove all RPCBridge references; agent is sole backend"
  ```

---

## Chunk 5: Wire `plugin.py` — no more pipe-guard cleanup dance

aerforge `plugin.py` already references `Agent` (not `RPCBridge`). Verify `unload()` is clean — no `disconnect_rpc()`, no `rpc.shutdown()`.

- [ ] **Step 1: Compare aerforge `plugin.py unload()` with aery_Qgis version**
  ```bash
  grep -A20 "def unload" /home/aryee/Desktop/aerforge/aery-qgis-plugin/aery_plugin/plugin.py
  grep -A20 "def unload" /home/aryee/Desktop/aery_Qgis/aery-qgis-plugin/aery_plugin/plugin.py
  ```

- [ ] **Step 2: Fix `Qgis` missing import in `_on_layers_added`**
  If `from qgis.core import Qgis` is missing:
  ```python
  # Add to plugin.py _on_layers_added's except block:
  from qgis.core import QgsMessageLog, Qgis
  QgsMessageLog.logMessage(f"Aery: layer-added notification failed: {e}", "Aery", Qgis.MessageLevel.Warning)
  ```

- [ ] **Step 3: Run tests**
  ```bash
  python3 -m pytest tests/ -q --tb=line
  ```

- [ ] **Step 4: Commit**
  ```bash
  git commit -am "fix(plugin): clean unload, add Qgis import guard"
  ```

---

## Chunk 6: Count down `test_improvements.py` failures

`test_improvements.py` today has 7 FAIL (out of 19). Target: 0 FAIL.

### Task 6.1: Fix `async for` mock failure

Fixes: `agent._client.chat_stream` returns `iter([...])` (sync iterator) but `run()` does `async for chunk in self._client.chat_stream(...)`. The mock needs an async iterator.

- [ ] **Step 1: Write failing test** (already exists: `TestToolUseSummaries::test_tool_use_summary_emitted_after_tool`)
- [ ] **Step 2: Fix the mock in test to return async iterator**
  ```python
  # In test_improvements.py, replace:
  agent._client.chat_stream = MagicMock(return_value=iter([...]))
  # With:
  async def _async_gen(items):
      for item in items:
          yield item
  agent._client.chat_stream = MagicMock(return_value=_async_gen([...]))
  ```
  (Apply fix to every test class that mocks `chat_stream`: TestToolUseSummaries, TestPostTurnSummaries, TestAPIRetryVisibility, TestThinkingExposure, TestPromptSuggestions — 5 tests)

- [ ] **Step 3: Run just the fixed tests**
  ```bash
  python3 -m pytest test_improvements.py -v --tb=short 2>&1 | grep -E "PASSED|FAILED"
  ```

### Task 6.2: Fix `_generate_prompt_suggestions` missing

- [ ] **Step 1: Write test for `test_suggestions_generated`**
  Read what the test expects: non-empty list of strings.

- [ ] **Step 2: Add `_generate_prompt_suggestions` to `agent.py`**
  Location: near `_build_system_prompt`, add:
  ```python
  def _generate_prompt_suggestions(self, last_response: str, session_id: str) -> list[str]:
      """Generate follow-up prompt suggestions from the last assistant response."""
      base = [
          "Explain what you just did",
          "Export the results",
          "Save this to a file",
      ]
      response_lower = last_response.lower()
      if "buffer" in response_lower or "buffer" in response_lower:
          base.extend(["Buffer with a different distance", "Intersect buffers with parcels"])
      if "select" in response_lower:
          base.extend(["Export selected features", "Count selected features"])
      if "export" in response_lower or "geojson" in response_lower:
          base.extend(["Convert to Shapefile", "Add to project"])
      if "layer" in response_lower:
          base.extend(["List all attributes", "Open attribute table"])
      return base[:6]
  ```

- [ ] **Step 3: Run `test_improvements.py`**
  Expected: 19 passed, 0 failed

- [ ] **Step 4: Commit**
  ```bash
  git commit -am "fix(test_improvements): 7 tests now pass (async iter + _generate_prompt_suggestions)"
  ```

---

## Chunk 7: QGIS E2E verification

### Task 7.1: Install and smoke test in QGIS

```bash
bash /home/aryee/Desktop/aerforge/aery-qgis-plugin/install.sh
```

Restart QGIS → Plugins → Manage and Install Plugins → Aery → enable it.

- [ ] Panel opens (dock widget visible)
- [ ] No crash in QGIS syslog
- [ ] "Aery" appears in the Plugins menu

### Task 7.2: End-to-end conversation test

Scenario table:

| # | Scenario | Expected |
|---|----------|----------|
| E1 | "What layers are in this project?" | Agent responds with layer list |
| E2 | "Buffer roads by 50m" | `run_qgis_code` executed, new buffer layer in project |
| E3 | "Delete all layers" | Permission dialog or deny |
| E4 | Type bogus API key, send | `api_retry` event visible, graceful failure |
| E5 | Save project, restart QGIS, open Aery | Session restored |
| E6 | Click activity indicator | Shows current state |
| E7 | Click Settings → change model → OK | Agent reinitializes without crash |

For any failure:
1. Note exact error in found_issues.md
2. Fix in aerforge
3. Re-test in QGIS
4. Commit

### Task 7.3: Uninstall cleanly

```bash
bash /home/aryee/Desktop/aerforge/aery-qgis-plugin/uninstall.sh
```

Restart QGIS → Aery absent from plugin manager → clean.

---

## Chunk 8: Final state verification

### Task 8.1: All unit tests green

```bash
python3 -m pytest tests/ test_improvements.py -q --tb=line
```
Expected: **≥ 190 pass, 0 fail**

### Task 8.2: Git log is clean linear history

```bash
git log --oneline | head -20
```
No loose branches, no merge commits. Each chunk = 1-3 clean linear commits.

### Task 8.3: `test_improvements.py` = 0 FAIL

```bash
python3 -m pytest test_improvements.py -q --tb=line 2>&1 | tail -3
```

### Task 8.4: aerforge metadata bumped

Before finalizing: bump `version=` in `metadata.txt` from `0.7.2` to the target (e.g. `1.2.0-alpha`).

### Task 8.5: Create `FINAL_SYNC.md`

Write to `/home/aryee/Desktop/aerforge/aery-qgis-plugin/FINAL_SYNC.md`:
- What was migrated from aery_Qgis
- What was built new in aerforge
- Known open issues
- Current test counts
- Last tested QGIS version

---

## Quick reference: key files and their status

| File | aerforge status | aery_Qgis better? |
|------|----------------|-------------------|
| `agent.py` | New, 657-line Python agent | N/A — new |
| `llm_client.py` | New, 1112-line direct HTTP | N/A — new |
| `tools.py` | New, 795-line toolchain + permissions | N/A — new |
| `session.py` | New, disk-backed session | N/A — new |
| `chat_panel.py` | Rewritten (1653 lines, smaller than aery_Qgis 1661) | needs event merge |
| `plugin.py` | Rewritten (135 lines, uses Agent) | no rpc bridge refs needed |
| `rpc_bridge.py` | Present but not used by chat_panel | potentially keep for compat |
| `qgis_executor.py` | Working | likely neutral |
| `oauth_helper.py` | Working | likely neutral |
| `smoke_test.py` | May still have broken import | copy fix if present |
| `uninstall.sh` | Likely missing `set -e` | copy fix |
