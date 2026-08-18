import json
import secrets
import socket
import threading
import time
import queue
import uuid
from typing import Optional
from aery_plugin.logger import logger


class SocketServer:
    """Runs a local TCP socket server to accept execution requests.

    Every connection must include the per-instance auth_token in its JSON
    request. The token is generated at server startup (or provided) and exposed
    through the executor so the legitimate runner can retrieve it via the same
    out-of-band channel used for the port number (e.g. argv or a runner config
    file).

    Improvements over legacy inline handler:
    - Single canonical handler (no duplication between SocketServer and
      QGISCodeExecutor)
    - Auth token shared with executor (per-instance, not per-server)
    - 300s deadline with progress-event skipping (keeps UI responsive)
    - `default=str` in JSON dump for non-serializable objects
    """

    def __init__(
        self,
        executor,
        auth_token: Optional[str] = None,
    ):
        self.executor = executor
        self.server: Optional[socket.socket] = None
        self.port: Optional[int] = None
        # Per-instance random secret (32 bytes => ~43 chars urlsafe base64).
        # Can be provided externally so executor and server share the same token.
        self.auth_token: str = auth_token or secrets.token_urlsafe(32)
        self._server_thread: Optional[threading.Thread] = None
        self._running = False


    def start(self):
        self._running = True
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.server.listen(5)
        self.server.settimeout(1.0)

        self._server_thread = threading.Thread(target=self._serve, daemon=True)
        self._server_thread.start()

    def _serve(self):
        while self._running:
            try:
                conn, _ = self.server.accept()
                threading.Thread(target=self._handle_connection, args=(conn,), daemon=True).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_connection(self, conn: socket.socket):
        _req_id: Optional[str] = None
        try:
            MAX_BODY = 1_048_576
            data = b""
            conn.settimeout(30.0)
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
                if len(data) >= MAX_BODY:
                    break
                if b"\n" in data:
                    break

            request = json.loads(data.decode().strip())
            # Authenticate the request. The token is per-instance and must be
            # shared out-of-band with the legitimate runner (same channel as port).
            if request.get("auth_token") != self.auth_token:
                conn.sendall((json.dumps({
                    "id": request.get("id") or str(uuid.uuid4()),
                    "success": False,
                    "error": "Unauthorized: missing or invalid auth_token",
                }, default=str) + "\n").encode())
                return
            req_id = request.get("id") or str(uuid.uuid4())
            _req_id = req_id
            method = request.get("method", "run_code")
            code = request.get("code", "")

            result_queue: queue.Queue = queue.Queue()
            self.executor._result_queues[req_id] = result_queue
            metadata = {
                "method": method,
                "tool_name": request.get("tool_name") or method,
                "source": request.get("source", "plugin"),
                "started_at": time.perf_counter(),
                "run_id": request.get("run_id") or self.executor.run_id,
            }

            if method == "get_project_context":
                # Special bypass for immediate project context resolution
                ctx = self.executor._get_project_context()
                result_queue.put({"id": req_id, "success": True, "result": ctx})
                try:
                    from aery_plugin.graph_engine import record_layer, build_tool_capability_graph
                    import os
                    project_path = ctx.get("project_path", "")
                    pdir = ctx.get("project_dir", os.path.expanduser("~"))
                    build_tool_capability_graph(pdir)
                    for lyr in ctx.get("layers", []):
                        record_layer(pdir, lyr["name"], lyr.get("type", ""), lyr.get("crs", ""))
                except Exception as e:
                    logger.debug("executor_socket: graph recording failed: %s", e)
            elif method == "question":
                self.executor._priority_queue.append((req_id, "__ask_user__", result_queue, metadata))
            elif method == "capture_canvas":
                self.executor._priority_queue.append((req_id, "__capture_canvas__", result_queue, metadata))
            else:
                self.executor._normal_queue.put((req_id, code, result_queue, metadata))

            # Wait for final result, skipping progress events (300s deadline)
            deadline_prog = time.monotonic() + 300
            result = None
            while time.monotonic() < deadline_prog:
                try:
                    item = result_queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                if item.get("type") == "progress":
                    continue
                result = item
                break
            if result is None:
                raise queue.Empty
            conn.sendall((json.dumps(result, default=str) + "\n").encode())
        except queue.Empty:
            conn.sendall((json.dumps({"success": False, "error": "Execution timed out after 300s"}, default=str) + "\n").encode())
        except Exception as e:
            conn.sendall((json.dumps({"success": False, "error": str(e)}, default=str) + "\n").encode())
        finally:
            try:
                conn.close()
            except Exception as e:
                logger.debug("executor_socket: close connection failed: %s", e)
            if _req_id is not None:
                self.executor._result_queues.pop(_req_id, None)

    def shutdown(self):
        self._running = False
        if self.server:
            try:
                self.server.shutdown(socket.SHUT_RDWR)
            except OSError as e:
                logger.debug("executor_socket: server shutdown failed: %s", e)
            try:
                self.server.close()
            except OSError as e:
                logger.debug("executor_socket: server close failed: %s", e)
            self.server = None
        if self._server_thread and self._server_thread.is_alive():
            self._server_thread.join(timeout=1.0)