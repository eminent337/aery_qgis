import threading
from typing import Optional
from aery_plugin.logger import logger

class PermissionManager:
    """Manages the agent's permission state machine for destructive actions."""

    def __init__(self):
        # We now track requests in dictionaries by request_id
        self._requests: dict[str, dict] = {}
        self._lock = threading.Lock()
        self.always: bool = False
        # Per-session flag: once the user explicitly approves a `run_qgis_code`
        # call (or ticks "always allow for this session"), subsequent non-
        # destructive code executions in the same session are auto-approved.
        # This keeps the safety net visible (the first code execution always
        # prompts) without making every tool call a click-through.
        self.code_approved: bool = False

    def _clear_requests(self) -> None:
        """Deny-and-drop every pending request.

        Sets each pending event (approved=False) before removing the request,
        so a blocked ``wait_for_approval`` can never strand the agent thread
        when requests are cleared without an explicit UI resolve.
        """
        for req in self._requests.values():
            req["approved"] = False
            req["event"].set()
        self._requests.clear()

    def reset(self) -> None:
        """Clear stale permission state — call at the start of each turn.
        We deliberately do NOT reset `self.always` here. When the user ticks
        "always allow for this session", that decision must survive across
        turns until the session is explicitly reset. Per-turn clearing was
        breaking the session-wide bypass after the first turn.
        """
        with self._lock:
            self._clear_requests()
            # self.always and code_approved are session-scoped; keep them alive
            # across turns. They are cleared only by reset_session().

    def reset_session(self) -> None:
        """Clear all session-scoped permission state.
        Call when starting a new session (new project, "Reset" button, etc.)
        so that the first code execution in the new session prompts again.
        """
        with self._lock:
            self._clear_requests()
            self.always = False
            self.code_approved = False

    def register_request(self, request_id: str) -> None:
        """Register a new permission request."""
        with self._lock:
            self._requests[request_id] = {
                "event": threading.Event(),
                "approved": False,
                "always": False
            }

    def resolve(self, request_id: str, approved: bool, always: bool = False) -> None:
        """Called from UI when user approves/denies pending request."""
        with self._lock:
            req = self._requests.get(request_id)
            if not req:
                return
            req["approved"] = approved
            req["always"] = always
            if always:
                self.always = True
            req["event"].set()

    def cancel(self, request_id: str) -> None:
        """Called from UI when user explicitly denies."""
        self.resolve(request_id, approved=False)

    def cancel_all(self) -> None:
        """Called when the user clicks the global Stop button to abort all tasks."""
        with self._lock:
            for req in self._requests.values():
                req["approved"] = False
                req["event"].set()

    def wait_for_approval(self, request_id: str, timeout: Optional[float] = None) -> bool:
        """Block until UI resolves the permission request.

        ``timeout=None`` (the default) waits indefinitely: a permission
        request stays pending until the user approves/denies or the run is
        stopped (cancel_all). An explicit timeout — used only by headless or
        automation callers that have no UI to answer — returns False when it
        elapses so the agent never hangs forever without a human.
        """
        with self._lock:
            req = self._requests.get(request_id)
            if not req:
                return False
            event = req["event"]

        try:
            event.wait(timeout=timeout)
        except Exception as e:
            logger.debug("agent_permissions: wait_for_approval event wait failed: %s", e)

        with self._lock:
            req = self._requests.get(request_id)
            if not req:
                return False
            return req["approved"]

    def mark_code_approved(self) -> None:
        """Mark run_qgis_code as approved for the rest of this session.

        Called by the dispatcher when the user approves a non-destructive
        code execution (or destructive code, in which case `always` is also
        set). Subsequent non-destructive `run_qgis_code` calls will be
        auto-allowed without prompting.
        """
        with self._lock:
            self.code_approved = True
