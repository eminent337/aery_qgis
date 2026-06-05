"""Agent core for the Aery QGIS plugin.

Manages the conversation loop, tool calling, and context building.
Calls LLM APIs directly via llm_client.py.
"""

import warnings
warnings.warn("agent.py is deprecated. Use AeryEngine in engine/core.py instead.", DeprecationWarning)

from aery_plugin.logger import logger
import asyncio
import json
import os
import threading
import time
import uuid
from typing import Any, Callable, Optional

from aery_plugin.llm_client import create_client, APIError
from aery_plugin.tools import ToolRegistry
from aery_plugin.prompts import build_system_prompt
from aery_plugin.agent_permissions import PermissionManager
from aery_plugin.agent_context import ContextBuilder
from aery_plugin.agent_dispatcher import ToolDispatcher

try:
    from PyQt6.QtCore import QObject, pyqtSignal, QThread
    _HAS_PYQT6 = True
except ImportError:
    _HAS_PYQT6 = False
    QObject = object
    pyqtSignal = None
    QThread = None


class _AgentWorker(QObject):
    """Runs Agent.run() in a QThread so the Qt event loop stays responsive.

    Uses the opengeos/GeoAgent pattern: a brand-new asyncio event loop is
    created inside the worker thread — never touches the Qt main-loop.
    """
    finished = pyqtSignal(str)   # final assistant text
    error   = pyqtSignal(str)   # error string
    chunk   = pyqtSignal(dict)  # streaming event dict

    def __init__(self, agent):
        super().__init__()
        self._agent = agent
        self._user_message = ""

    def start_task(self, user_message: str) -> None:
        self._user_message = user_message

    def run(self) -> None:  # QThread.run() override
        logger.debug(f"[Aery Worker] run() started, message: {self._user_message[:50]!r}")
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            reply = loop.run_until_complete(
                self._agent.run(self._user_message, on_event=self.chunk.emit)
            )
            logger.debug(f"[Aery Worker] finished, reply: {reply[:100]!r}")
            self.finished.emit(reply)
        except Exception as e:
            logger.debug(f"[Aery Worker] error: {e}")
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))
        finally:
            loop.close()


class Agent(QObject):
    """The geospatial AI agent."""

    # Signals for ChatPanel to connect to (once, at init)
    finished = pyqtSignal(str) if _HAS_PYQT6 else None
    error = pyqtSignal(str) if _HAS_PYQT6 else None

    def __init__(self, executor, iface=None):
        super().__init__()
        self.executor = executor
        self.iface = iface
        self.tools = ToolRegistry(executor, iface, agent=self)
        self._messages: list[dict] = []
        self._client = None
        self._model = ""
        self._provider_id = ""
        self._system_prompt = build_system_prompt()
        self._session_id: Optional[str] = None
        self._project_dir: Optional[str] = None
        self.permissions = PermissionManager()
        self.context_builder = ContextBuilder()
        self.dispatcher = ToolDispatcher(self)

        self._lock = threading.RLock()
        self._client_lock = threading.Lock()

        self._undo_stack: list[dict] = []
        self._self_correction_count: int = 0
        self._tool_loop_count: int = 0
        self._last_tool_hash: str = ""
        self._consecutive_tool_name: str = ""
        self._consecutive_tool_count: int = 0

        self._last_layer_hash: str = ""  # track layer state changes
        self._max_context_messages: int = 40  # trim history to this many messages

        # QThread worker for offloading async agent work off the Qt main thread
        if _HAS_PYQT6:
            self._worker = _AgentWorker(self)
            self._thread = QThread()
            self._worker.moveToThread(self._thread)
            self._thread.started.connect(self._worker.run)
            self._worker.finished.connect(self._on_worker_finished)
            self._worker.error.connect(self._on_worker_error)
            self._thread.finished.connect(self._on_thread_finished)
        else:
            self._worker = None
            self._thread = None

    def _on_thread_finished(self) -> None:
        """Clean up after thread finishes so it can be restarted."""
        pass  # QThread can be restarted via start() after it finishes

    def cancel(self) -> None:
        """Cancel the current agent turn if running."""
        self.cancel_permission()
        with self._lock:
            if self._thread and self._thread.isRunning():
                self._thread.quit()
                self._thread.wait(500)

    def start(self, user_message: str) -> None:
        """Post a user message and start processing in the QThread worker.

        If the worker is already running, quits the thread and restarts it
        with the new message.
        """
        logger.info(f"[Aery Agent] start() called, message={user_message[:50]!r}, has_worker={self._worker is not None}, thread_running={self._thread.isRunning() if self._thread else 'N/A'}")
        with self._lock:
            if self._worker:
                if self._thread.isRunning():
                    logger.info(f"[Aery Agent] restarting thread...")
                    self._thread.quit()
                    self._thread.wait(1000)
                self._worker.start_task(user_message)
                logger.info(f"[Aery Agent] starting thread...")
                self._thread.start()
            else:
                import warnings
                warnings.warn("PyQt6 not available; agent.run() must be called directly")

    def is_busy(self) -> bool:
        """Return True if the agent thread is currently active."""
        return self._thread.isRunning() if self._thread else False

    # ── QThread worker callbacks (forwarded via Agent signals) ──────────────
    def _on_worker_finished(self, reply: str) -> None:
        """Called in the Qt main thread when the worker completes successfully."""
        if self.finished:
            self.finished.emit(reply)

    def _on_worker_error(self, error: str) -> None:
        """Called in the Qt main thread when the worker raises an exception."""
        if self.error:
            self.error.emit(error)

    def resolve_permission(self, request_id: str, approved: bool, always: bool = False) -> None:
        """Called by UI to resolve a pending permission request."""
        self.permissions.resolve(request_id, approved, always)

    def cancel_permission(self) -> None:
        """Called from ChatPanel when the user explicitly denies."""
        self.permissions.cancel()

    def reset_permission_state(self) -> None:
        """Clear stale permission state — call at the start of each turn."""
        self.permissions.reset()

    def _layers_changed(self) -> bool:
        return self.context_builder.layers_changed()

    def _build_context_message(self, user_query: str = "") -> str:
        msg = self.context_builder.build_context_message(user_query, self._project_dir)
        # Handle graph engine side-effects separately from string building
        if self._project_dir and self.context_builder.layers_changed():
            try:
                from aery_plugin.graph_engine import build_tool_capability_graph, prune_graph
                import threading as _threading
                try:
                    from aery_plugin.graph_engine import auto_detect_spatial_relationships, collect_layer_data_for_spatial
                    layer_data = collect_layer_data_for_spatial()
                    _threading.Thread(
                        target=auto_detect_spatial_relationships,
                        args=(self._project_dir, layer_data),
                        daemon=True,
                    ).start()
                except Exception:
                    logger.info("Spatial relationship auto-detection failed in graph engine background thread")
                build_tool_capability_graph(self._project_dir)
                prune_graph(self._project_dir)
            except Exception:
                logger.info("Graph build or prune failed")
        return msg

    def _trim_messages(self):
        """Keep message history within limits to reduce context cost.

        Applies both count-based and size-based trimming:
        - Drops oldest messages beyond the count limit
        - Truncates oversized tool results to prevent context overflow
        - Compacts earliest exchanges when total estimated tokens exceed threshold
          (replacing dropped tool results with compact summaries so the LLM
          retains awareness of what was done without the full payload)
        """
        MAX_COUNT = self._max_context_messages
        MAX_TOOL_CHARS = 8000  # raised: gives agent more context on errors/results
        COMPACT_TOKENS = 80_000

        # 1. Truncate oversized tool results in-place
        for msg in self._messages:
            if msg.get("role") == "tool" and isinstance(msg.get("content"), str):
                if len(msg["content"]) > MAX_TOOL_CHARS:
                    msg["content"] = msg["content"][:MAX_TOOL_CHARS] + " ...[truncated]"

        # 2. Strip thinking blocks from assistant messages to save tokens
        for msg in self._messages:
            if msg.get("role") == "assistant" and isinstance(msg.get("content"), list):
                new_content = [b for b in msg["content"] if isinstance(b, dict) and b.get("type") != "thinking"]
                if not new_content and not msg.get("tool_calls"):
                    new_content = ""
                elif len(new_content) == 1 and isinstance(new_content[0], dict) and new_content[0].get("type") == "text":
                    new_content = new_content[0].get("text", "")
                msg["content"] = new_content

        # 3. Rough token estimate (chars / 4)
        total_chars = sum(len(str(m.get("content", ""))) for m in self._messages)
        total_tokens = total_chars // 4

        # 3. Count-based trim
        if len(self._messages) <= MAX_COUNT and total_tokens <= COMPACT_TOKENS:
            return

        # 4. Size-based compaction: replace oldest exchange pairs with summaries
        while len(self._messages) > MAX_COUNT or total_tokens > COMPACT_TOKENS:
            if len(self._messages) < 2:
                break
            # Pop the oldest message (user message or already-compacted tool result)
            dropped = self._messages.pop(0)
            total_chars -= len(str(dropped.get("content", "")))
            total_tokens = total_chars // 4

            # Compact tool results in-place and discard assistant tool-call wrappers
            while self._messages and self._messages[0].get("role") in ("tool", "assistant"):
                msg = self._messages[0]
                if msg.get("role") == "assistant":
                    # Discard tool-call-only wrappers (no meaningful text content)
                    content = msg.get("content")
                    has_text = bool(content) and str(content).strip() != ""
                    if msg.get("tool_calls") and not has_text:
                        self._messages.pop(0)
                        continue
                    break  # text assistant response — keep it
                # Tool result — compact in-place (keep the compacted version)
                tool_name = msg.get("name", "tool")
                content = str(msg.get("content", ""))
                old_len = len(content)
                summary = content[:120].rstrip() + ("..." if len(content) > 120 else "")
                msg["content"] = f"[Compacted] {tool_name} completed — {summary}"
                total_chars -= old_len - len(msg["content"])
                total_tokens = total_chars // 4
                break  # compact one tool result, recheck on next outer iteration

    def _try_failover(self):
        """Try to create a failover client using alternative providers.

        Returns a new client if failover is available, None otherwise.
        """
        try:
            from aery_plugin import oauth_helper
            from aery_plugin.llm_client import create_client

            # Get list of available providers with credentials
            active = oauth_helper.get_active_provider()
            if not active:
                return None

            current_provider = active.get("id", "")
            auth = oauth_helper._load_auth()

            # Try common failover providers
            failover_providers = ["openai", "anthropic", "google", "deepseek", "groq"]

            for provider_id in failover_providers:
                if provider_id == current_provider:
                    continue

                # Check if provider has credentials
                auth_entry = auth.get(provider_id, {})
                if not auth_entry:
                    continue

                # Try to create client
                try:
                    client, model = create_client(provider_id, auth_entry, active.get("model", ""))
                    if client:
                        return client
                except Exception:
                    logger.info("Failover client creation failed for provider %s", provider_id)
                    continue

        except Exception:
            logger.info("Failover iteration failed while searching for alternative provider")

        return None

    def _load_credentials(self) -> tuple[str, dict, str]:
        """Load provider credentials from oauth_helper.

        Returns (provider_id, auth_entry, model).
        """
        from aery_plugin import oauth_helper

        active = oauth_helper.get_active_provider()
        if not active:
            raise RuntimeError("No LLM provider configured. Open Settings to configure a provider.")

        provider_id = active["id"]
        model = active.get("model", "")

        auth = oauth_helper._load_auth()
        auth_entry = auth.get(provider_id, {})

        self._provider_id = provider_id
        return provider_id, auth_entry, model

    def initialize(self):
        """Set up the API client from current provider config."""
        provider_id, auth_entry, model = self._load_credentials()
        self._client, self._model = create_client(provider_id, auth_entry, model)

    def reinitialize(self):
        """Force re-create the API client (call after provider/model change).

        Closes the previous client's HTTP connections first to prevent
        resource leaks over long-running QGIS sessions.
        """
        import asyncio
        if self._client is not None and hasattr(self._client, "close"):
            try:
                asyncio.get_event_loop().run_until_complete(self._client.close())
            except Exception:
                logger.debug("Client connection close failed during reinitialize")
        self._client = None
        self._model = ""
        self.initialize()



    @staticmethod
    def _diagnose_error(error_str: str, tool_name: str = "") -> str:
        """Return a targeted diagnosis hint to prepend to the error message.

        Helps the LLM understand root cause without re-reading a wall of
        traceback text. Maps common error patterns to actionable fixes.
        """
        s = error_str.lower()
        hints = []

        if "crs" in s or "coordinate" in s or ("reproject" in s and "fail" in s):
            hints.append("[Hint] CRS mismatch detected. Reproject layers to the same CRS before spatial operations.")
        if "invalid geometry" in s or "topolog" in s:
            hints.append("[Hint] Invalid geometry. Run processing.run('native:fixgeometries', ...) first.")
        if "featurecount" in s and "0" in s or "empty" in s:
            hints.append("[Hint] Layer appears empty. Check layer.featureCount() > 0 before processing.")
        if "no module named" in s or "importerror" in s or "modulenotfounderror" in s:
            mod = ""
            import re as _re
            m = _re.search(r"no module named '([^']+)'", s)
            if m:
                mod = m.group(1)
            hints.append(f"[Hint] Missing Python package{' ' + repr(mod) if mod else ''}. Use the install_package tool to install it.")
        if "permissionerror" in s or "access denied" in s or "errno 13" in s:
            hints.append("[Hint] File permission error. Check that the output path is writable (use project_dir, not /tmp or /).")
        if "timeout" in s or "timed out" in s:
            hints.append("[Hint] Operation timed out. Try splitting into smaller chunks or processing a subset of the data.")
        if "keyerror" in s or "'" in s and "not in" in s:
            hints.append("[Hint] Key or field not found. Print layer.fields().names() to see available field names.")
        if "isvalid" in s or "not valid" in s or "layer invalid" in s:
            hints.append("[Hint] Layer is invalid. Check layer.error().message() for the specific QGIS error.")
        if "output" in s and ("exists" in s or "overwrite" in s):
            hints.append("[Hint] Output file already exists. Add overwrite=True or delete the file first.")

        if hints:
            return "\n".join(hints) + "\n" + error_str
        # No specific hint — return a cleaned traceback (last 15 lines)
        lines = error_str.strip().splitlines()
        if len(lines) > 20:
            return "\n".join(lines[-15:])
        return error_str

    def _get_model_max_tokens(self) -> int:
        """Look up the current model's maximum output token limit from the provider registry."""
        try:
            from aery_plugin.providers import get_model
            model_def = get_model(self._provider_id, self._model)
            if model_def and hasattr(model_def, "max_tokens") and model_def.max_tokens:
                return int(model_def.max_tokens)
        except Exception:
            logger.debug("Model max_tokens lookup failed for %s/%s", self._provider_id, self._model)
        return 8192  # safe default

    async def run(self, user_message: str, on_event: Optional[Callable] = None) -> str:
        """Run the agent with a user message.

        on_event: callback for streaming events (tool calls, text chunks).
        Returns the final assistant response text.
        """
        logger.info(f"[Aery Agent] run() called, model={self._model!r}, provider={self._provider_id!r}")
        from aery_plugin.prompts import build_system_prompt
        self._system_prompt = build_system_prompt(user_message)
        with self._client_lock:
            if not self._client:
                try:
                    self.initialize()
                    logger.info(f"[Aery Agent] initialized, client={type(self._client).__name__}, model={self._model!r}")
                except Exception as e:
                    import traceback
                    traceback.print_exc()
                    raise

        # Auto-detect project_dir from QGIS if not set by start_session()
        if not self._project_dir:
            try:
                from qgis.core import QgsProject
                _ppath = QgsProject.instance().fileName()
                if _ppath:
                    import os as _os
                    self._project_dir = _os.path.dirname(_ppath)
            except Exception:
                logger.debug("Project directory auto-detection from QGIS failed")

        # Build environment context and inject into system prompt
        # (instead of as user messages, so it doesn't pollute conversation history)
        base_prompt = self._system_prompt
        ctx = self._build_context_message(user_message)
        if ctx:
            base_prompt += f"\n\n=== LIVE QGIS ENVIRONMENT ===\n{ctx}"
        self._system_prompt = base_prompt

        with self._lock:
            self._messages.append({"role": "user", "content": user_message})
        self._persist_message({"role": "user", "content": user_message})

        # Record prompt in graph
        if self._project_dir:
            try:
                from aery_plugin.graph_engine import record_prompt
                record_prompt(self._project_dir, user_message, [], [])
            except Exception as e:
                logger.error(f"[Aery agent] record_prompt: {e}")

        max_turns = 15  # raised from 10: complex multi-step tasks need more room
        for turn in range(max_turns):
            self.reset_permission_state()
            if on_event:
                on_event({"type": "thinking"})

            # Build messages with system prompt
            api_messages = [{"role": "system", "content": self._system_prompt}] + self._messages
            if turn == 0:
                logger.debug(f"[Aery Debug] Messages: user_msgs={len(self._messages)}")
            elif turn == 1:
                import json as _json
                for i, m in enumerate(self._messages):
                    role = m.get("role", "?")
                    tcs = m.get("tool_calls", [])
                    logger.debug(f"[Aery Debug] msg[{i}]: role={role} tcs={len(tcs)} content={'yes' if m.get('content') else 'no'}")

            # Call LLM with streaming
            try:
                _query_parts = [user_message]
                _last_assistant = next(
                    (m["content"] for m in reversed(self._messages) if m.get("role") == "assistant" and m.get("content")),
                    ""
                )
                if _last_assistant:
                    _query_parts.append(_last_assistant)
                _query = " | ".join(_query_parts)
                tools = self.tools.retrieve_tools(query=_query)
                full_content = ""
                tool_calls = []
                # Use the model's actual max_tokens from the provider registry
                _max_tokens = self._get_model_max_tokens()
                logger.info(f"[Aery Agent] calling chat_stream, model={self._model!r} provider={self._provider_id!r} tools={len(tools)} max_tokens={_max_tokens}")
                chunk_count = 0

                # Stream the response
                async for chunk in self._client.chat_stream(
                    messages=api_messages,
                    model=self._model,
                    max_tokens=_max_tokens,
                    tools=tools if tools else None,
                    provider=self._provider_id,
                    session_id=self._session_id or "",
                ):
                    chunk_count += 1
                    if chunk_count == 1:
                        logger.info(f"[Aery Agent] first chunk keys={list(chunk.keys())}")
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    # Extract text content
                    content = delta.get("content", "")
                    if content:
                        full_content += content
                        if on_event:
                            on_event({"type": "text_chunk", "text": content})
                    # Extract tool calls (may arrive in separate chunks)
                    if delta.get("tool_calls"):
                        for tc in delta["tool_calls"]:
                            # Merge with existing tool calls by index
                            idx = tc.get("index", 0)
                            while len(tool_calls) <= idx:
                                tool_calls.append({"id": "", "function": {"name": "", "arguments": ""}})
                            existing = tool_calls[idx]
                            if tc.get("id"):
                                existing["id"] = tc["id"]
                            if tc.get("function"):
                                if tc["function"].get("name"):
                                    existing["function"]["name"] += tc["function"]["name"]
                                if tc["function"].get("arguments"):
                                    existing["function"]["arguments"] += tc["function"]["arguments"]

                logger.info(f"[Aery Agent] stream ended, chunks={chunk_count} full_content_len={len(full_content)} tool_calls={len(tool_calls)}")
                # Filter out provider-internal tool calls (e.g. default_api: for Gemini)
                before = len(tool_calls)
                tool_calls = self._client.filter_tool_calls(tool_calls)
                if len(tool_calls) < before:
                    logger.info(f"[Aery Agent] filtered {before - len(tool_calls)} provider-internal tool calls")
                if not full_content and not tool_calls:
                    # Fallback: non-streaming response (some providers don't stream tools well)
                    logger.info(f"[Aery Agent] trying non-streaming fallback chat()")
                    response = await self._client.chat(
                        messages=api_messages,
                        model=self._model,
                        max_tokens=_max_tokens,
                        tools=tools if tools else None,
                        provider=self._provider_id,
                        session_id=self._session_id or "",
                    )
                    choice = response.get("choices", [{}])[0]
                    message = choice.get("message", {})
                    full_content = message.get("content", "")
                    tool_calls = message.get("tool_calls", [])

            except APIError as e:
                # Try failover on retryable errors (429, 500, 502, 503, 504)
                if e.retryable or e.status_code in (429, 500, 502, 503, 504):
                    try:
                        failover_client = self._try_failover()
                        if failover_client:
                            self._client = failover_client
                            if on_event:
                                on_event({"type": "text_chunk", "text": f"[Failover] Switching to alternative provider due to: {e}\n"})
                            continue  # Retry with new provider
                    except Exception:
                        logger.info("Failover attempt failed when trying alternative provider")
                return f"API error: {e}"

            if tool_calls:
                # Loop detection: two strategies
                # 1. Exact same tool calls (same names + arguments)
                import hashlib as _hashlib
                _call_hash = _hashlib.md5(
                    json.dumps([{"n": tc.get("function", {}).get("name", ""),
                                 "a": tc.get("function", {}).get("arguments", "")}
                                for tc in tool_calls], sort_keys=True).encode()
                ).hexdigest()
                _looping = False
                if _call_hash == self._last_tool_hash:
                    self._tool_loop_count += 1
                    if self._tool_loop_count >= 3:
                        _looping = True
                else:
                    self._tool_loop_count = 0
                    self._last_tool_hash = _call_hash
                # 2. Same tool name repeated consecutively (even with different args)
                if not _looping:
                    _names = sorted({tc.get("function", {}).get("name", "") for tc in tool_calls})
                    _joined = ",".join(_names)
                    if _joined == self._consecutive_tool_name:
                        self._consecutive_tool_count += 1
                        if self._consecutive_tool_count >= 3:
                            _looping = True
                    else:
                        self._consecutive_tool_name = _joined
                        self._consecutive_tool_count = 0
                if _looping:
                    msg = "Agent stopped: repeated tool calls detected (loop). Try rephrasing your request."
                    if on_event:
                        on_event({"type": "text_chunk", "text": f"\n[{msg}]\n"})
                    return msg

                logger.info(f"[Aery Agent] executing {len(tool_calls)} tool_calls in parallel...")

                exec_results, _turn_snapshots = await self.dispatcher.execute_all(tool_calls, on_event)

                # Reset loop counter when any tool errored
                if any(h_err for _, _, _, h_err in exec_results):
                    self._tool_loop_count = 0

                # Push per-turn undo group (all snapshots from this turn as one entry)
                if _turn_snapshots:
                    self._undo_stack.append({
                        "type": "turn_group",
                        "snapshots": list(_turn_snapshots),
                        "timestamp": self._get_timestamp(),
                    })
                    if len(self._undo_stack) > 15:
                        self._undo_stack.pop(0)

                for tc, name, tool_result, had_error in exec_results:
                    pair = self._client.format_message_pair(tc, tool_result)
                    self._messages.extend(pair)
                    self._persist_message({
                        "role": "tool", "tool_call_id": tc.get("id", ""),
                        "tool_name": name, "content": tool_result[:8000],
                    })
                self._trim_messages()
            else:
                # Final response
                if full_content:
                    self._messages.append({"role": "assistant", "content": full_content})
                    self._persist_message({"role": "assistant", "content": full_content})
                return full_content

        return "Agent reached maximum turns."

    def reset(self):
        """Clear conversation history and start fresh session."""
        with self._lock:
            # Stop worker thread if running
            if self._thread and self._thread.isRunning():
                self._thread.quit()
                self._thread.wait(500)
            self._messages = []
            self._undo_stack = []
            self._self_correction_count = 0
            self._tool_loop_count = 0
            self._last_tool_hash = ""
            self._consecutive_tool_name = ""
            self._consecutive_tool_count = 0
            # New session — clear per-session code approval so the first
            # run_qgis_code in this new session re-prompts.
            self.permissions.reset_session()
            if self._project_dir:
                from aery_plugin.session import create_session
                self._session_id = create_session(self._project_dir)

    def get_history(self) -> list[dict]:
        """Return conversation history."""
        with self._lock:
            return list(self._messages)

    def list_sessions(self, project_dir: str) -> list[dict]:
        """List all sessions for a project."""
        from aery_plugin.session import list_sessions
        return list_sessions(project_dir)

    def start_session(self, project_dir: str) -> str:
        """Start a new persisted session. Returns session ID."""
        with self._lock:
            from aery_plugin.session import create_session
            self._project_dir = project_dir
            self._session_id = create_session(project_dir)
            return self._session_id

    def resume_session(self, project_dir: str, session_id: str) -> list[dict]:
        """Resume a previous session. Returns loaded messages."""
        with self._lock:
            from aery_plugin.session import load_session, load_agent_state
            self._project_dir = project_dir
            self._session_id = session_id
            messages = load_session(project_dir, session_id)
            # Filter out session_start headers and context messages
            self._messages = [
                m for m in messages
                if m.get("role") in ("user", "assistant", "tool")
            ]
            
            # Restore internal state
            state = load_agent_state(project_dir, session_id)
            self._undo_stack = state.get("undo_stack", [])
            self._tool_loop_count = state.get("tool_loop_count", 0)
            self._last_tool_hash = state.get("last_tool_hash", "")
            self._consecutive_tool_name = state.get("consecutive_tool_name", "")
            self._consecutive_tool_count = state.get("consecutive_tool_count", 0)
            
            # Restore bypassPermissions mode
            if state.get("bypass_permissions", False):
                self.permissions.always = True
                self.tools.set_permission_mode("bypassPermissions")
            else:
                self.permissions.always = False
                self.tools.set_permission_mode("strict")
                
            return list(self._messages)

    def _persist_state(self):
        """Save internal state to disk for resumption."""
        if not self._session_id or not self._project_dir:
            return
        from aery_plugin.session import save_agent_state
        state = {
            "undo_stack": self._undo_stack,
            "tool_loop_count": self._tool_loop_count,
            "last_tool_hash": self._last_tool_hash,
            "consecutive_tool_name": self._consecutive_tool_name,
            "consecutive_tool_count": self._consecutive_tool_count,
            "bypass_permissions": getattr(self.permissions, 'always', False)
        }
        save_agent_state(self._project_dir, self._session_id, state)

    def _persist_message(self, msg: dict):
        """Persist a message to the session file."""
        with self._lock:
            if not self._session_id or not self._project_dir:
                return
            from aery_plugin.session import append_message
            append_message(self._project_dir, self._session_id, msg)
            self._persist_state()

    def _snapshot_layer_state(self, tool_name: str, code: str) -> dict:
        """Capture a lightweight layer snapshot for undo support."""
        snapshot = {"tool": tool_name, "code": code, "timestamp": self._get_timestamp(), "layers": {}}
        try:
            from qgis.core import QgsProject
            for lyr in QgsProject.instance().mapLayers().values():
                snapshot["layers"][lyr.id()] = {
                    "name": lyr.name(),
                    "type": lyr.type().name,
                }
        except Exception:
            logger.debug("Layer state snapshot failed")
        return snapshot

    def _get_timestamp(self) -> str:
        """Return an ISO-format timestamp."""
        import datetime
        return datetime.datetime.utcnow().isoformat() + "Z"
