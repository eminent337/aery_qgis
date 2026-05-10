"""JSON-RPC bridge between QGIS plugin and Aery agent subprocess."""

import json
import os
import signal
import subprocess
import tempfile
import threading
from typing import Any, Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal


def _find_aery_binary() -> str:
    """Find the Aery binary.

    Priority:
    1. Bundled binary next to this plugin file
    2. System `aery` command (as fallback, user must have Node.js)
    """
    plugin_dir = os.path.dirname(os.path.abspath(__file__))
    bundled = os.path.join(plugin_dir, "bin", "aery-qgis-runner")
    if os.path.isfile(bundled) and os.access(bundled, os.X_OK):
        return bundled
    return "aery"


class RPCBridge(QObject):
    """Manages Aery subprocess and JSON-RPC communication.

    Spawns the Aery standalone binary, passes provider config via temp file
    (--provider-file arg), then communicates via stdin/stdout JSON-RPC.
    Events from Aery are dispatched as PyQt signals.
    """

    event_received = pyqtSignal(dict)
    response_received = pyqtSignal(str, dict)
    error_occurred = pyqtSignal(str)
    process_exited = pyqtSignal(int)

    def __init__(
        self,
        cwd: str,
        port: int,
        provider_config: Optional[dict] = None,
        parent: Optional[QObject] = None,
    ):
        super().__init__(parent)
        self._cwd = cwd
        self._port = port
        self._provider_config = provider_config or {}
        self._process: Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread] = None
        self._provider_file: Optional[str] = None
        self._running = False
        self._pending_responses: dict[str, Callable] = {}

    def _write_provider_file(self) -> Optional[str]:
        """Write provider config to a temp file. Returns the file path."""
        if not self._provider_config:
            return None
        fd, path = tempfile.mkstemp(prefix="aery_provider_", suffix=".json")
        os.close(fd)
        with open(path, "w") as f:
            json.dump(self._provider_config, f)
        os.chmod(path, 0o600)  # Secure: only owner can read
        return path

    def spawn(self):
        """Launch Aery in RPC mode."""
        binary = _find_aery_binary()
        is_bundled = binary != "aery"

        # Write provider config to temp file (binary reads and deletes it)
        provider_file = self._write_provider_file()

        try:
            if is_bundled:
                cmd = [binary, str(self._port)]
                if provider_file:
                    cmd.extend(["--provider-file", provider_file])
                self._process = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self._cwd,
                    text=True,
                )
            else:
                self._process = subprocess.Popen(
                    ["aery", "--mode", "rpc"],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=self._cwd,
                    text=True,
                )
            self._running = True

            # Reader thread for stdout
            self._reader_thread = threading.Thread(
                target=self._read_stdout, daemon=True
            )
            self._reader_thread.start()

            # Stderr reader
            stderr_thread = threading.Thread(
                target=self._read_stderr, daemon=True
            )
            stderr_thread.start()

        except FileNotFoundError:
            self.error_occurred.emit(
                "Aery not found. Install with: npm install -g @eminent337/aery"
            )
        except Exception as e:
            self.error_occurred.emit(f"Failed to start Aery: {e}")

    def send_command(self, command: dict[str, Any], callback: Optional[Callable] = None):
        """Send a JSON-RPC command to Aery via stdin."""
        if not self._process or not self._running:
            self.error_occurred.emit("Aery is not running")
            return

        cmd_id = command.get("id")
        if callback and cmd_id:
            self._pending_responses[cmd_id] = callback

        self._process.stdin.write(json.dumps(command) + "\n")
        self._process.stdin.flush()

    def prompt(self, message: str):
        """Send a prompt command."""
        self.send_command({"type": "prompt", "message": message})

    def abort(self):
        """Abort the current operation."""
        self.send_command({"type": "abort"})

    def send_command(self, command: dict[str, Any], callback: Optional[Callable] = None):
        """Send a JSON-RPC command to Aery via stdin."""
        if not self._process or not self._running:
            self.error_occurred.emit("Aery is not running")
            return

        cmd_id = command.get("id")
        if callback and cmd_id:
            self._pending_responses[cmd_id] = callback

        line = json.dumps(command) + "\n"
        self._process.stdin.write(line)
        self._process.stdin.flush()

    def prompt(self, message: str):
        """Send a prompt command to the agent."""
        self.send_command({
            "type": "prompt",
            "message": message,
        })

    def abort(self):
        """Abort the current agent operation."""
        self.send_command({"type": "abort"})

    def _read_stdout(self):
        """Read JSON lines from Aery's stdout."""
        while self._running and self._process:
            try:
                line = self._process.stdout.readline()
                if not line:
                    break

                data = json.loads(line.strip())
                self._dispatch_event(data)

            except json.JSONDecodeError:
                continue
            except Exception as e:
                if self._running:
                    self.error_occurred.emit(f"RPC error: {e}")
                break

        if self._process:
            exit_code = self._process.poll()
            self.process_exited.emit(exit_code if exit_code is not None else 1)

    def _read_stderr(self):
        """Read and log Aery's stderr."""
        while self._running and self._process:
            try:
                line = self._process.stderr.readline()
                if not line:
                    break
                print(f"[Aery stderr] {line.strip()}")
            except Exception:
                break

    def _dispatch_event(self, data: dict[str, Any]):
        """Route an RPC event to the appropriate handler."""
        event_type = data.get("type")

        if event_type == "response":
            cmd = data.get("command", "")
            cmd_id = data.get("id")
            if cmd_id and cmd_id in self._pending_responses:
                self._pending_responses[cmd_id](data)
                del self._pending_responses[cmd_id]
            self.response_received.emit(cmd, data)
        else:
            # Streaming events (message_start, tool_execution, etc.)
            self.event_received.emit(data)

    def shutdown(self):
        """Terminate the Aery subprocess gracefully."""
        self._running = False
        if self._process:
            try:
                self._process.send_signal(signal.SIGTERM)
                self._process.wait(timeout=5)
            except Exception:
                try:
                    self._process.kill()
                    self._process.wait(timeout=2)
                except Exception:
                    pass
            self._process = None
