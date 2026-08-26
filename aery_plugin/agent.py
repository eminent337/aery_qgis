"""Agent core for the Aery QGIS plugin.

Manages the conversation loop, tool calling, and context building.
Calls LLM APIs directly via llm_client.py.
"""

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


# Agent state machine for plan-first workflow
class AgentState:
    IDLE = "idle"
    PLANNING = "planning"
    CONFIRMED = "confirmed"
    EXECUTING = "executing"
    WAITING_APPROVAL = "waiting_approval"
    VERIFYING = "verifying"
    DONE = "done"


# Tools classified as destructive — always require per-step approval
DESTRUCTIVE_TOOLS = frozenset({
    "delete_layers",
    "project.write",
    "layer.commitChanges",
    "rename_layer",
})


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
        self._agent._cancel_event = asyncio.Event()
        try:
            reply = loop.run_until_complete(
                self._agent.run(self._user_message, on_event=self.chunk.emit)
            )
            logger.debug(f"[Aery Worker] finished, reply: {reply[:100]!r}")
            self.finished.emit(reply)
        except asyncio.CancelledError:
            logger.debug("[Aery Worker] cancelled")
            self.error.emit("[cancelled by user]")
        except Exception as e:
            logger.debug(f"[Aery Worker] error: {e}")
            import traceback
            traceback.print_exc()
            self.error.emit(str(e))
        finally:
            self._agent._cancel_event = None
            try:
                # Cancel and await any pending tasks left behind by async generators or timeouts
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception as e:
                logger.debug(f"[Aery Worker] cleanup error: {e}")
            loop.close()


class Agent(QObject):
    """The geospatial AI agent."""

    # Signals for ChatPanel to connect to (once, at init)
    finished = pyqtSignal(str) if _HAS_PYQT6 else None
    error = pyqtSignal(str) if _HAS_PYQT6 else None

    # Plan-first workflow signals
    _step_approved = pyqtSignal(str) if _HAS_PYQT6 else None   # emits tool_name when user approves a destructive step
    _step_cancelled = pyqtSignal(str) if _HAS_PYQT6 else None  # emits reason when user cancels

    def __init__(self, executor, iface=None, session_context=None):
        super().__init__()
        self.executor = executor
        self.iface = iface
        self.session_context = session_context
        self.tools = ToolRegistry(executor, iface, agent=self)
        from aery_plugin.knowledge_base import KnowledgeBase
        self._knowledge = KnowledgeBase()
        self._messages: list[dict] = []
        self._client = None
        self._model = ""
        self._provider_id = ""
        addendum = getattr(self, "_active_profile", None)
        addendum_text = addendum.system_prompt_addendum if addendum else ""
        self._system_prompt = build_system_prompt("", addendum_text)
        self._session_id: Optional[str] = None
        self._project_dir: Optional[str] = None
        self.permissions = PermissionManager()
        self.context_builder = ContextBuilder()
        # Audit trail — initialized lazily on first session start
        self._audit_logger = None
        self._trace_id: Optional[str] = None
        # Session isolation via SessionManager (vault namespaces per session)
        self._session_manager = None
        self.dispatcher = ToolDispatcher(self)

        self._lock = threading.RLock()
        self._client_lock = threading.Lock()
        self._cancelled = False
        self._cancel_event: Optional[asyncio.Event] = None

        self._undo_stack: list[dict] = []
        self._self_correction_count: int = 0
        self._tool_loop_count: int = 0
        self._last_tool_hash: str = ""
        self._consecutive_tool_name: str = ""
        self._consecutive_tool_count: int = 0
        # History of tool-name signatures for oscillation detection (A-B-A-B loops
        # evade simple hash/sequence checks because the signature alternates).
        self._tool_history: list[tuple[str, ...]] = []
        self._state = AgentState.IDLE
        self._pending_plan: str = ""
        self._pending_steps: list = []
        self._current_step_index: int = 0
        self._retry_count: int = 0
        self._approval_event: Optional[asyncio.Event] = None  # paused destructive step await
        self._approval_result: bool = False  # True=approved, False=cancelled

        self._last_layer_hash: str = ""  # track layer state changes
        self._max_context_messages: int = 40  # trim history to this many messages

        self._next_message: Optional[str] = None

        # QThread worker for offloading async agent work off the Qt main thread
        if _HAS_PYQT6:
            self._worker = _AgentWorker(self)
            self._thread = QThread()
            self._worker.moveToThread(self._thread)
            self._thread.started.connect(self._worker.run)
            self._worker.finished.connect(self._on_worker_finished)
            self._worker.error.connect(self._on_worker_error)
        else:
            self._worker = None
            self._thread = None

    def _set_state(self, state: str) -> None:
        """Transition to a new state machine state."""
        self._state = state

    def _is_destructive(self, tool_name: str) -> bool:
        """Return True if the tool is classified as destructive and requires per-step approval."""
        return tool_name in DESTRUCTIVE_TOOLS

    async def _execute_next_step(self, on_event: Optional[Callable] = None) -> tuple[str, bool]:
        """Execute one step from _pending_steps.
        
        Returns (result_or_error, should_pause).
        - should_pause is True when a destructive tool requested approval.
        - On success, advances _current_step_index.
        - When all steps done, sets state to VERIFYING.
        """
        if self._current_step_index >= len(self._pending_steps):
            self._set_state(AgentState.VERIFYING)
            return ("All steps complete.", False)

        step = self._pending_steps[self._current_step_index]
        tool_name = step.get("function", {}).get("name", "")

        if self._is_destructive(tool_name):
            # Destructive tools always require user approval
            self._approval_event = asyncio.Event()
            self._set_state(AgentState.WAITING_APPROVAL)
            self._step_approved.emit(tool_name)
            # Wait for user to approve in chat_panel (which calls resume_from_approval)
            try:
                await asyncio.wait_for(self._approval_event.wait(), timeout=300)
            except asyncio.TimeoutError:
                self._set_state(AgentState.IDLE)
                return ("Approval timed out after 5 minutes.", False)
            self._approval_event = None
            if not self._approval_result:
                return (f"Step cancelled by user.", False)
            self._approval_result = False  # reset for next time

        # Execute yolo step
        self._set_state(AgentState.EXECUTING)
        exec_results, _turn_snapshots = await self.dispatcher.execute_all([step], on_event)
        _, name, tool_result, had_error = exec_results[0]

        with self._lock:
            tc = step
            pair = self._client.format_message_pair(tc, tool_result)
            self._messages.extend(pair)
            self._persist_message({
                "role": "tool", "tool_call_id": tc.get("id", ""),
                "tool_name": name, "content": tool_result[:8000],
            })

        if had_error:
            try:
                from aery_plugin.error_classifier import wrap_tool_error
                wrapped = wrap_tool_error(name, tool_result[:500])
            except Exception:
                wrapped = f"Tool error: {tool_result[:500]}"
            return (wrapped, False)

        self._current_step_index += 1
        if self._current_step_index >= len(self._pending_steps):
            self._set_state(AgentState.VERIFYING)

        return (tool_result, False)

    def resume_from_approval(self) -> None:
        """Called by ChatPanel when user approves a destructive step.

        Sets the approval event, which unblocks _execute_next_step's await.
        """
        if self._approval_event is not None:
            self._approval_event.set()

    def _restart_after_finish(self) -> None:
        """One-shot slot: called when the thread exits after a deferred restart.

        Disconnects itself, then re-invokes start() with the queued message.
        """
        try:
            self._thread.finished.disconnect(self._restart_after_finish)
        except Exception:
            pass  # already disconnected
        msg = self._next_message
        self._next_message = None
        if msg:
            self.start(msg)

    def cancel(self) -> None:
        """Cancel the current agent turn if running."""
        self.cancel_permission()
        # Ask the executor to cancel any in-flight QGIS processing task
        # (e.g. a QgsProcessingAlgRunnerTask) cooperatively.
        try:
            self.executor.cancel()
        except Exception:
            pass
        with self._lock:
            self._cancelled = True
        # Signal the asyncio loop in the worker thread
        if self._cancel_event is not None:
            try:
                loop = self._cancel_event._loop
                loop.call_soon_threadsafe(self._cancel_event.set)
            except Exception:
                pass
        with self._lock:
            if self._thread and self._thread.isRunning():
                self._thread.quit()
                self._thread.wait(500)

    def start_with_images(self, user_message: str, images: list[tuple[str, str]]) -> None:
        """Post a user prompt with attached base64 image data URLs for multimodal vision models."""
        # Format message content as multimodal array: [{"type": "text", "text": ...}, {"type": "image_url", ...}]
        content_blocks: list[dict] = []
        if user_message:
            content_blocks.append({"type": "text", "text": user_message})
        for file_path, data_url in images:
            content_blocks.append({
                "type": "image_url",
                "image_url": {"url": data_url},
            })
        with self._lock:
            self._messages.append({"role": "user", "content": content_blocks})
        self.start(user_message)

    def start(self, user_message: str) -> None:
        """Post a user message and start processing in the QThread worker.

        If the worker is already running, quits the thread and restarts it
        with the new message.  If quit() times out the thread is still busy;
        in that case the message is stored and a deferred restart is scheduled
        via the finished signal.
        """
        logger.info(f"[Aery Agent] start() called, message={user_message[:50]!r}, has_worker={self._worker is not None}, thread_running={self._thread.isRunning() if self._thread else 'N/A'}")
        with self._lock:
            if self._worker:
                if self._thread.isRunning():
                    logger.info(f"[Aery Agent] thread busy — scheduling deferred restart...")
                    self._next_message = user_message
                    self._thread.finished.connect(self._restart_after_finish)
                    self._thread.quit()
                    if not self._thread.wait(1000):
                        # Timed out — the deferred slot will call start() again
                        # once the thread actually exits, so return here.
                        logger.info(f"[Aery Agent] quit() timed out; restart deferred via finished signal")
                        return
                    # Thread exited cleanly before we hit the timeout — clean up
                    # the slot we just connected and run synchronously.
                    try:
                        self._thread.finished.disconnect(self._restart_after_finish)
                    except Exception:
                        pass
                    self._next_message = None
                self._worker.start_task(user_message)
                logger.info(f"[Aery Agent] starting thread...")
                self._thread.start()
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
        """Called from ChatPanel when the user explicitly aborts the run."""
        self.permissions.cancel_all()

    def reset_permission_state(self) -> None:
        """Clear stale permission state — call at the start of each turn."""
        self.permissions.reset()

    def _layers_changed(self) -> bool:
        return self.context_builder.layers_changed()

    def invalidate_project_context(self) -> None:
        """Invalidate the cached project snapshot after QGIS state changes."""
        self._last_layer_hash = ""
        self.tools.invalidate_project_context()

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

        with self._lock:
            # 1. Truncate oversized tool results in-place
            for msg in self._messages:
                if msg.get("role") == "tool" and isinstance(msg.get("content"), str):
                    if len(msg["content"]) > MAX_TOOL_CHARS:
                        msg["content"] = msg["content"][:MAX_TOOL_CHARS] + " ...[truncated]"
                elif msg.get("role") == "user" and isinstance(msg.get("content"), list):
                    for block in msg["content"]:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            if isinstance(block.get("content"), str) and len(block["content"]) > MAX_TOOL_CHARS:
                                block["content"] = block["content"][:MAX_TOOL_CHARS] + " ...[truncated]"

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

                # Pop the oldest message
                dropped = self._messages.pop(0)
                total_chars -= len(str(dropped.get("content", "")))
                total_tokens = total_chars // 4

                # If we dropped an Anthropic tool_use, we MUST drop the subsequent tool_result!
                while self._messages:
                    msg = self._messages[0]

                    # Drop OpenAI tool calls and empty Anthropic tool uses
                    if msg.get("role") == "assistant":
                        content = msg.get("content")
                        has_text = bool(content) and str(content).strip() != ""
                        is_anth_tool_use = isinstance(content, list) and len(content) > 0 and content[0].get("type") == "tool_use"
                        if (msg.get("tool_calls") and not has_text) or is_anth_tool_use:
                            dropped = self._messages.pop(0)
                            total_chars -= len(str(dropped.get("content", "")))
                            total_tokens = total_chars // 4
                            continue
                        break  # text assistant response — keep it

                    # Compact OpenAI tool results
                    if msg.get("role") == "tool":
                        tool_name = msg.get("name", "tool")
                        content = str(msg.get("content", ""))
                        old_len = len(content)
                        summary = content[:120].rstrip() + ("..." if len(content) > 120 else "")
                        msg["role"] = "user"
                        msg["content"] = f"[Compacted] [System: Tool '{tool_name}' completed execution] {summary}"
                        msg.pop("tool_call_id", None)
                        msg.pop("name", None)
                        total_chars -= old_len - len(msg["content"])
                        total_tokens = total_chars // 4
                        break

                    # Compact Anthropic tool results
                    if msg.get("role") == "user" and isinstance(msg.get("content"), list):
                        blocks = msg["content"]
                        if blocks and isinstance(blocks[0], dict) and blocks[0].get("type") == "tool_result":
                            content = str(blocks[0].get("content", ""))
                            old_len = len(str(msg["content"]))
                            summary = content[:120].rstrip() + ("..." if len(content) > 120 else "")
                            msg["content"] = f"[Compacted] [System: Tool completed execution] {summary}"
                            total_chars -= old_len - len(msg["content"])
                            total_tokens = total_chars // 4
                            break

                    break  # Normal user message, keep it

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

            # Try common failover providers
            failover_providers = ["openai", "anthropic", "google", "deepseek", "groq"]

            for provider_id in failover_providers:
                if provider_id == current_provider:
                    continue

                # Check if provider has credentials
                auth_entry = oauth_helper.get_auth_entry(provider_id)
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
        """Load provider credentials from oauth_helper using active profile.

        Returns (provider_id, auth_entry, model).
        """
        from aery_plugin import oauth_helper
        from aery_plugin.profiles import (
            list_profiles, get_default_profile_id, select_active_profile
        )

        # Get profiles and select active one
        profiles = list_profiles()
        default_id = get_default_profile_id()
        active_profile = select_active_profile(
            profiles=profiles,
            default_profile_id=default_id,
            selected_profile_id=getattr(self, "_selected_profile_id", None),
            user_explicitly_chose=bool(getattr(self, "_selected_profile_id", None)),
        )

        if not active_profile:
            raise RuntimeError("No LLM profile configured. Open Settings to create a profile.")

        provider_id = active_profile.provider
        model = active_profile.model

        auth_entry = oauth_helper.get_auth_entry(provider_id)

        # Store profile for use in prompt building and tool filtering
        self._active_profile = active_profile
        self._provider_id = provider_id
        # Wire profile policy into PolicyEngine and ToolRegistry
        try:
            from aery_plugin.policy import get_policy_engine, Policy
            if active_profile.policy:
                policy_name = f'profile:{active_profile.id}'
                p = Policy.from_dict(active_profile.policy)
                get_policy_engine().add_policy(policy_name, p)
                self._policy_name = policy_name
            else:
                self._policy_name = None
        except Exception:
            self._policy_name = None
        # Update tool registry's policy reference
        if hasattr(self, 'tools') and self.tools:
            self.tools._policy_name = self._policy_name
        return provider_id, auth_entry, model

    def initialize(self):
        """Set up the API client from current provider config."""
        provider_id, auth_entry, model = self._load_credentials()
        self._client, self._model = create_client(provider_id, auth_entry, model)

    def reinitialize(self):
        """Force re-create the API client (call after provider/model change).

        Closes the previous client's HTTP connections first to prevent
        resource leaks over long-running QGIS sessions.
        Also re-selects the active profile to pick up any profile changes.
        """
        import asyncio
        if self._client is not None and hasattr(self._client, "close"):
            try:
                asyncio.get_event_loop().run_until_complete(self._client.close())
            except Exception:
                logger.debug("Client connection close failed during reinitialize")
        self._client = None
        self._model = ""
        # Clear cached profile to force re-selection
        if hasattr(self, "_active_profile"):
            delattr(self, "_active_profile")
        if hasattr(self, "_selected_profile_id"):
            delattr(self, "_selected_profile_id")
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
            hints.append(f"[Hint] Missing Python package{' ' + repr(mod) if mod else ''}. Use the pip_install tool to install it.")
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
        self._cancelled = False
        # Plan-first: reset state variables at start of run
        self._state = AgentState.PLANNING
        self._retry_count = 0
        # Start audit trace for this agent run
        self._trace_id = None
        try:
            from aery_plugin.telemetry import get_collector
            self._trace_id = get_collector().start_trace("agent_run")
        except Exception:
            pass
        self._pending_plan = ""
        self._pending_steps = []
        self._current_step_index = 0
        # Reset loop counters at start of every run so a previous loop
        # detection doesn't immediately false-trigger on the next prompt.
        self._tool_loop_count = 0
        self._last_tool_hash = ""
        self._consecutive_tool_name = ""
        self._consecutive_tool_count = 0
        self._tool_history.clear()
        from aery_plugin.prompts import build_system_prompt, classify_task_profile
        task_profile = classify_task_profile(user_message)
        # Exact greetings/thanks/help requests do not need an LLM, tools, QGIS
        # context, or a planning loop. Reply locally and immediately.
        if task_profile == "chat":
            reply = "Hi — what would you like to do in QGIS?"
            with self._lock:
                self._messages.append({"role": "user", "content": user_message})
                self._messages.append({"role": "assistant", "content": reply})
            self._persist_message({"role": "user", "content": user_message})
            self._persist_message({"role": "assistant", "content": reply})
            self._set_state(AgentState.IDLE)
            if on_event:
                on_event({"type": "text_chunk", "text": reply})
            return reply
        addendum = getattr(self, "_active_profile", None)
        addendum_text = addendum.system_prompt_addendum if addendum else ""
        self._system_prompt = build_system_prompt(user_message, addendum_text)
        with self._client_lock:
            if not self._client:
                try:
                    self.initialize()
                    logger.info(f"[Aery Agent] initialized, client={type(self._client).__name__}, model={self._model!r}")
                except Exception:
                    logger.exception("[Aery Agent] client initialization failed")
                    raise
        # Auto-detect project_dir from QGIS if not set by start_session().
        if not self._project_dir:
            try:
                from qgis.core import QgsProject
                _ppath = QgsProject.instance().fileName()
                if _ppath:
                    import os as _os
                    self._project_dir = _os.path.dirname(_ppath)
            except Exception:
                logger.debug("Project directory auto-detection from QGIS failed")
        # Direct actions use their compact prompt as-is. Knowledge injection is
        # only needed for complex analysis work. The live QGIS context is no
        # longer automatically injected into the system prompt; this keeps the
        # system prompt static (enabling 2x-3x faster prompt caching on the LLM
        # provider) and encourages the model to query get_project_context()
        # only when it actually needs it.
        base_prompt = self._system_prompt
        if task_profile == "complex":
            try:
                _kb_snippets = self._knowledge.format_and_cap(user_message, max_chars=1500)
                if _kb_snippets:
                    base_prompt += f"\n\n=== QGIS API KNOWLEDGE ===\n{_kb_snippets}"
            except Exception as e:
                logger.debug(f"[Aery Agent] Knowledge injection skipped: {e}")
        # Do not force a user-visible PLAN/STEPS/SUCCESS preamble. Aery is an
        # action agent: simple work should call the matching QGIS tool directly.
        self._system_prompt = base_prompt

        with self._lock:
            # Avoid pushing a duplicate plain-text message if start_with_images already queued the multimodal content
            if not self._messages or self._messages[-1].get("role") != "user":
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
            with self._lock:
                if self._cancelled:
                    self._cancelled = False  # reset for next run
                    return "[cancelled by user]"
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
                prof = getattr(self, "_active_profile", None)
                allowlist = prof.tool_allowlist if prof and prof.tool_allowlist else None
                tools = self.tools.retrieve_tools(query=_query, tool_allowlist=allowlist)
                full_content = ""
                tool_calls = []
                # Use the model's actual max_tokens from the provider registry
                _max_tokens = self._get_model_max_tokens()
                logger.info(f"[Aery Agent] calling chat_stream, model={self._model!r} provider={self._provider_id!r} tools={len(tools)} max_tokens={_max_tokens}")
                chunk_count = 0

                # Stream the response
                # Get model params from active profile
                profile = getattr(self, "_active_profile", None)
                model_params = profile.model_params if profile else {}
                temperature = model_params.get("temperature", 0.0)
                max_tokens_override = model_params.get("max_tokens", _max_tokens)

                async for chunk in self._client.chat_stream(
                    messages=api_messages,
                    model=self._model,
                    max_tokens=max_tokens_override,
                    temperature=temperature,
                    tools=tools if tools else None,
                    provider=self._provider_id,
                    session_id=self._session_id or "",
                ):
                    if self._cancel_event is not None and self._cancel_event.is_set():
                        raise asyncio.CancelledError()
                    chunk_count += 1
                    if chunk_count == 1:
                        logger.info(f"[Aery Agent] first chunk keys={list(chunk.keys())}")
                    choice = chunk.get("choices") or [{}]
                    if not choice:
                        continue
                    delta = choice[0].get("delta", {})
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
                                tool_calls.append({"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                            existing = tool_calls[idx]
                            if tc.get("id"):
                                existing["id"] = tc["id"]
                            if tc.get("function"):
                                if tc["function"].get("name"):
                                    existing["function"]["name"] += tc["function"]["name"]
                                if tc["function"].get("arguments"):
                                    existing["function"]["arguments"] += tc["function"]["arguments"]
                                    try:
                                        self._speculative_validate(existing["function"]["name"], existing["function"]["arguments"])
                                    except Exception as e:
                                        logger.debug(f"[Speculative validation error]: {e}")
                logger.info(f"[Aery Agent] stream ended, chunks={chunk_count} full_content_len={len(full_content)} tool_calls={len(tool_calls)}")
                # Record LLM call telemetry
                try:
                    from aery_plugin.telemetry import get_collector
                    _provider = self._provider_id or "unknown"
                    _model = self._model or "unknown"
                    _response_id = str(uuid.uuid4())[:8]
                    get_collector().record_llm_call(
                        provider=_provider, model=_model,
                        latency_ms=chunk_count * 50,  # rough estimate
                        completion_tokens=len(full_content.split()),
                        success=not (self._cancelled),
                        finish_reason="tool_calls" if tool_calls else "stop",
                        response_id=_response_id,
                        is_streaming=True,
                        temperature=temperature,
                        max_tokens=max_tokens_override,
                    )
                except Exception:
                    pass
                before = len(tool_calls)
                tool_calls = self._client.filter_tool_calls(tool_calls)
                if len(tool_calls) < before:
                    logger.info(f"[Aery Agent] filtered {before - len(tool_calls)} provider-internal tool calls")

                # Plan-first: parse PLAN/STEPS/SUCCESS from first response
                if self._state == AgentState.PLANNING and full_content:
                    import re as _re
                    # Fast-path: single non-destructive tool -> execute directly
                    if len(tool_calls) == 1 and not self._is_destructive(tool_calls[0]['function']['name']):
                        logger.info(f"[Aery Agent] fast-path: single non-destructive tool, executing directly")
                    else:
                        _plan_m = _re.search(r"PLAN:\s*(.+?)(?:\nSTEPS:|\nSUCCESS:|$)", full_content, _re.DOTALL)
                        if _plan_m and tool_calls:
                            self._pending_plan = _plan_m.group(1).strip()
                            self._pending_steps = list(tool_calls)
                            self._current_step_index = 0
                            self._set_state(AgentState.CONFIRMED)

                if not full_content and not tool_calls:
                    # Fallback: non-streaming response (some providers don't stream tools well)
                    logger.info(f"[Aery Agent] trying non-streaming fallback chat()")
                    profile = getattr(self, "_active_profile", None)
                    model_params = profile.model_params if profile else {}
                    temperature = model_params.get("temperature", 0.0)
                    max_tokens_override = model_params.get("max_tokens", _max_tokens)

                    response = await self._client.chat(
                        messages=api_messages,
                        model=self._model,
                        max_tokens=max_tokens_override,
                        temperature=temperature,
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
                msg = str(e)
                if e.status_code == 402 or "402" in msg:
                    return (
                        "API Error 402 (Payment Required): Your active LLM provider or API key has run out of credits/quota. "
                        "Please check your account billing or select/configure a different model/provider in the Aery settings."
                    )
                return f"API error: {e}"

            if tool_calls:
                # Loop detection
                import hashlib
                tool_str = "".join(f"{tc.get('function', {}).get('name')}:{tc.get('function', {}).get('arguments')}" for tc in tool_calls)
                tool_hash = hashlib.md5(tool_str.encode()).hexdigest()
                
                if tool_hash == self._last_tool_hash:
                    self._tool_loop_count += 1
                else:
                    self._tool_loop_count = 0
                self._last_tool_hash = tool_hash
                
                tool_names = [tc.get('function', {}).get('name') for tc in tool_calls]
                if len(tool_names) == 1:
                    tname = tool_names[0]
                    if tname == self._consecutive_tool_name:
                        self._consecutive_tool_count += 1
                    else:
                        self._consecutive_tool_name = tname
                        self._consecutive_tool_count = 1
                else:
                    self._consecutive_tool_name = ""
                    self._consecutive_tool_count = 0
                # 2-turn oscillation detection (A-B-A-B patterns where both the
                # tool names and arguments are identical on alternating turns).
                self._tool_history.append(tool_hash)
                if len(self._tool_history) > 4:
                    self._tool_history.pop(0)
                oscillating = (
                    len(self._tool_history) >= 4
                    and self._tool_history[0] == self._tool_history[2]
                    and self._tool_history[1] == self._tool_history[3]
                    and self._tool_history[0] != self._tool_history[1]
                )
                if (
                    self._tool_loop_count >= 2
                    or oscillating
                ):
                    logger.warning("[Aery Agent] Loop detected. Terminating execution.")
                    return "Loop detected: The agent is calling the same tools repeatedly. Terminating execution to prevent infinite loop. Try rephrasing your prompt."
                if self._pending_steps:
                    # Plan-first mode (safe): execute steps one by one
                    _step_result, _should_pause = await self._execute_next_step(on_event)
                    if _should_pause:
                        continue  # Waiting for destructive-tool approval
                    if _step_result.startswith("Tool error:"):
                        if self._retry_count < 2:
                            self._retry_count += 1
                            _error_msg = _step_result
                            # Prepend error context and ask LLM for fix
                            _fix_prompt = f"Previous step failed: {_error_msg}\nProvide a corrected tool call to fix this. Respond with only the tool call."
                            _msgs_backup = list(self._messages)
                            self._messages.append({"role": "user", "content": _fix_prompt})
                            _retry_response = await self._client.chat(
                                messages=[{"role": "system", "content": self._system_prompt}] + self._messages[-10:],
                                model=self._model, max_tokens=256, temperature=0, tools=self.tools.retrieve_tools(query=_fix_prompt),
                                provider=self._provider_id, session_id=self._session_id or "",
                            )
                            _retry_tc = _retry_response.get("choices", [{}])[0].get("message", {}).get("tool_calls", [])
                            if _retry_tc:
                                self._pending_steps[self._current_step_index] = _retry_tc[0]
                            continue  # retry the step
                        else:
                            self._set_state(AgentState.IDLE)
                            return f"Step failed after 2 retries: {_step_result}"
                    # Step succeeded
                    continue  # next turn will call _execute_next_step again

                # Standard execution (no pending plan steps)
                logger.info(f"[Aery Agent] executing {len(tool_calls)} tool_calls in parallel...")

                exec_results, _turn_snapshots = await self.dispatcher.execute_all(tool_calls, on_event)

                # Push per-turn undo group (all snapshots from this turn as one entry)
                if _turn_snapshots:
                    self._undo_stack.append({
                        "type": "turn_group",
                        "snapshots": list(_turn_snapshots),
                        "timestamp": self._get_timestamp(),
                    })
                    if len(self._undo_stack) > 15:
                        self._undo_stack.pop(0)

                with self._lock:
                    for tc, name, tool_result, had_error in exec_results:
                        if had_error:
                            self._tool_loop_count = 0
                            self._consecutive_tool_count = 0
                            self._tool_history.clear()
                        pair = self._client.format_message_pair(tc, tool_result)
                        self._messages.extend(pair)
                        self._persist_message({
                            "role": "tool", "tool_call_id": tc.get("id", ""),
                            "tool_name": name, "content": tool_result[:8000],
                        })
                        # Record to audit trail
                        if self._audit_logger and self._project_dir:
                            try:
                                self._audit_logger.write_audit_entry(
                                    self._project_dir, req_id=name,
                                    code="", response={"success": not had_error},
                                    metadata={"tool_name": name, "run_id": self._session_id or ""}
                                )
                            except Exception:
                                pass
                    self._trim_messages()
            else:
                # Final response
                if full_content:
                    with self._lock:
                        self._messages.append({"role": "assistant", "content": full_content})
                    self._persist_message({"role": "assistant", "content": full_content})
                return full_content
        return "Agent reached maximum turns."
    def reset(self):
        """Clear conversation history and start fresh session."""
        # Flush audit trail before resetting
        if self._audit_logger:
            try:
                self._audit_logger.flush()
            except Exception:
                pass
            self._audit_logger = None
        self._trace_id = None
        # Unregister from SessionManager
        if self._session_manager and self._session_id:
            try:
                self._session_manager.remove_session(self._session_id)
            except Exception:
                pass
            self._session_manager = None
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
        from aery_plugin.session import create_session
        with self._lock:
            self._project_dir = project_dir
            self._session_id = create_session(project_dir)
        # Register with SessionManager for vault namespace isolation
        try:
            from aery_plugin.session_manager import get_session_manager
            mgr = get_session_manager()
            self._session_manager = mgr
            mgr.create_session(
                agent=self,
                vault_namespace=f"aery:{self._session_id}",
                metadata={"project_dir": project_dir},
            )
        except Exception as e:
            logger.debug(f"SessionManager registration failed: {e}")
        # Lazy-init audit trail on first real session
        if self._audit_logger is None and self._session_id:
            try:
                from aery_plugin.executor_audit import AuditLogger
                self._audit_logger = AuditLogger(run_id=self._session_id)
            except Exception as e:
                logger.debug(f"AuditLogger init failed: {e}")
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
            self._tool_history = [tuple(h) for h in state.get("tool_history", [])]
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
            "tool_history": [list(h) for h in self._tool_history],
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
        return datetime.datetime.now(datetime.UTC).isoformat().replace("+00:00", "Z")
    def get_session_vault(self):
        """Get the isolated vault for this session's namespace."""
        if self._session_manager and self._session_id:
            try:
                ctx = self._session_manager.get_session(self._session_id)
                if ctx:
                    return ctx.get_vault()
            except Exception:
                pass
        return None
    def get_session_list(self) -> list[dict]:
        """List all registered sessions from SessionManager."""
        if self._session_manager:
            try:
                return self._session_manager.list_sessions()
            except Exception:
                pass
        return []
    def get_audit_log_path(self) -> Optional[str]:
        """Return path to the audit log file, or None if not available."""
        if not self._audit_logger or not self._project_dir:
            return None
        audit_dir = self._audit_logger.get_audit_dir(self._project_dir)
        return os.path.join(audit_dir, "operations.jsonl")
    def get_llm_call_history(self) -> list[dict]:
        """Return LLM call records from telemetry."""
        try:
            from aery_plugin.telemetry import get_collector
            return get_collector().get_llm_calls()
        except Exception:
            return []

    def _try_parse_partial_json(self, s: str) -> dict:
        import json
        s = s.strip()
        if not s:
            return {}
        try:
            return json.loads(s)
        except Exception:
            pass
        # Try appending matching braces/brackets
        for suffix in ["}", '"}', '"]}', ']}']:
            try:
                return json.loads(s + suffix)
            except Exception:
                pass
        # Fallback to regex extraction of completed key-value pairs
        res = {}
        import re
        for m in re.finditer(r'"([^"]+)"\s*:\s*"([^"]*)"', s):
            res[m.group(1)] = m.group(2)
        for m in re.finditer(r'"([^"]+)"\s*:\s*(true|false|null|-?\d+(?:\.\d+)?)', s):
            val = m.group(2)
            if val == "true":
                res[m.group(1)] = True
            elif val == "false":
                res[m.group(1)] = False
            elif val == "null":
                res[m.group(1)] = None
            else:
                try:
                    res[m.group(1)] = float(val) if "." in val else int(val)
                except ValueError:
                    pass
        return res

    def _speculative_validate(self, name: str, arguments_str: str):
        if not name or not arguments_str:
            return
        parsed_args = self._try_parse_partial_json(arguments_str)
        if not parsed_args:
            return
        # Get semantic errors
        err = self.tools.validate_params(name, parsed_args)
        if err:
            real_errors = [e for e in err.split("; ") if "Missing required parameter" not in e]
            if real_errors:
                msg = "; ".join(real_errors)
                logger.info(f"[Aery Speculative Validation] semantic error detected during streaming: {msg}")
