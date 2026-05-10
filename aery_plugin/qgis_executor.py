"""Thread-safe QGIS Python code execution via local TCP socket + main-thread queue."""

import json
import queue
import socket
import threading
import traceback
from typing import Any, Optional

from PyQt6.QtCore import QObject, QTimer


class QGISCodeExecutor(QObject):
    """Executes Python code in QGIS's main thread safely.

    Starts a TCP socket server in a background thread. Requests arriving on
    the socket are queued and processed on the QGIS main thread via a QTimer.

    The executed code has full access to:
    - qgis.core, qgis.gui (QgsProject, QgsVectorLayer, etc.)
    - processing (Processing algorithms)
    - iface (QGIS interface)
    - PyQt6 (QWidget, etc.)
    """

    def __init__(self, iface: Optional[Any] = None):
        super().__init__()
        self.iface = iface
        self._request_queue: queue.Queue = queue.Queue()
        self._result_queues: dict[str, queue.Queue] = {}
        self._running = False
        self.server: Optional[socket.socket] = None
        self.port: Optional[int] = None
        self._server_thread: Optional[threading.Thread] = None
        self._timer: Optional[QTimer] = None

    def start_socket_server(self):
        """Start TCP socket server in background thread + main-thread QTimer."""
        self._running = True

        # TCP server on random port
        self.server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server.bind(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        self.server.listen(5)
        self.server.settimeout(1.0)

        self._server_thread = threading.Thread(target=self._serve, daemon=True)
        self._server_thread.start()

        # Main-thread timer for safe QGIS execution
        self._timer = QTimer()
        self._timer.timeout.connect(self._process_queue)
        self._timer.start(50)

    def _serve(self):
        """Accept socket connections in background thread."""
        while self._running:
            try:
                conn, _ = self.server.accept()
                threading.Thread(
                    target=self._handle_connection, args=(conn,), daemon=True
                ).start()
            except socket.timeout:
                continue
            except OSError:
                break

    def _handle_connection(self, conn: socket.socket):
        """Read a JSON request from socket and queue it for main-thread execution."""
        try:
            # Read until newline (streaming-safe)
            data = b""
            while not data.endswith(b"\n"):
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
            request = json.loads(data.decode().strip())
            req_id = request.get("id")
            method = request.get("method", "run_code")
            code = request.get("code", "")

            result_queue: queue.Queue = queue.Queue()
            self._result_queues[req_id] = result_queue

            if method == "get_project_context":
                # Direct execution - no code to run
                result_queue.put({
                    "id": req_id,
                    "success": True,
                    "result": self._get_project_context(),
                })
            else:
                self._request_queue.put((req_id, code, result_queue))

            # Wait for main thread to execute (or already done for get_project_context)
            result = result_queue.get(timeout=300)
            conn.sendall((json.dumps(result) + "\n").encode())
        except Exception as e:
            conn.sendall(
                (json.dumps({"success": False, "error": str(e)}) + "\n").encode()
            )
        finally:
            conn.close()

    def _process_queue(self):
        """Process queued requests on the main QGIS thread. Called by QTimer."""
        import os
        from qgis.core import QgsProject

        try:
            while True:
                req_id, code, result_queue = self._request_queue.get_nowait()
                try:
                    # Determine project directory
                    project_path = QgsProject.instance().fileName()
                    project_dir = os.path.dirname(project_path) if project_path else os.path.expanduser("~")

                    # Build execution context — project_dir always available
                    local_vars: dict[str, Any] = {
                        "iface": self.iface,
                        "project_dir": project_dir,
                        "result": None,
                    }
                    exec(code, self._get_globals(), local_vars)
                    result_queue.put(
                        {
                            "id": req_id,
                            "success": True,
                            "result": local_vars.get("result"),
                        }
                    )
                except Exception as e:
                    tb = traceback.format_exc()
                    result_queue.put(
                        {
                            "id": req_id,
                            "success": False,
                            "error": str(e),
                            "traceback": tb,
                        }
                    )
                finally:
                    self._result_queues.pop(req_id, None)
        except queue.Empty:
            pass

    def _get_project_context(self) -> dict[str, Any]:
        """Get current QGIS project state for the agent."""
        import os
        from qgis.core import Qgis

        project = self.iface.project()
        layers = []

        for layer in project.mapLayers().values():
            layer_info = {
                "id": layer.id(),
                "name": layer.name(),
                "type": layer.type().toString(),
                "crs": layer.crs().authid() if layer.crs() else None,
            }
            if hasattr(layer, "featureCount"):
                layer_info["feature_count"] = layer.featureCount()
            if hasattr(layer, "fields"):
                layer_info["fields"] = [f.name() for f in layer.fields()]
            if hasattr(layer, "geometryType"):
                layer_info["geometry_type"] = Qgis.geometryType(layer.wkbType()).toString()
            layers.append(layer_info)

        active_layer = self.iface.activeLayer()
        selection_count = 0
        if active_layer and hasattr(active_layer, "selectedFeatureIds"):
            selection_count = len(active_layer.selectedFeatureIds())

        # Determine project directory — all file operations should go here
        project_path = project.fileName()
        project_dir = os.path.dirname(project_path) if project_path else os.path.expanduser("~")

        return {
            "layers": layers,
            "active_layer": active_layer.name() if active_layer else None,
            "selection_count": selection_count,
            "project_crs": project.crs().authid() if project.crs() else None,
            "project_extent": str(project.extent().toString()),
            "project_dir": project_dir,
            "project_path": project_path or "",
        }

    def _get_globals(self) -> dict[str, Any]:
        """Return global imports available to executed code."""
        try:
            import processing
        except ImportError:
            processing = None
        from qgis.core import (
            QgsCoordinateReferenceSystem,
            QgsFeature,
            QgsField,
            QgsFields,
            QgsGeometry,
            QgsMapLayer,
            QgsMessageLog,
            QgsPointXY,
            QgsProcessingFeedback,
            QgsProject,
            QgsRasterLayer,
            QgsVectorFileWriter,
            QgsVectorLayer,
        )

        return {
            "processing": processing,
            "QgsProject": QgsProject,
            "QgsVectorLayer": QgsVectorLayer,
            "QgsRasterLayer": QgsRasterLayer,
            "QgsFeature": QgsFeature,
            "QgsGeometry": QgsGeometry,
            "QgsField": QgsField,
            "QgsFields": QgsFields,
            "QgsVectorFileWriter": QgsVectorFileWriter,
            "QgsCoordinateReferenceSystem": QgsCoordinateReferenceSystem,
            "QgsMessageLog": QgsMessageLog,
            "QgsProcessingFeedback": QgsProcessingFeedback,
            "QgsMapLayer": QgsMapLayer,
            "QgsPointXY": QgsPointXY,
        }

    def execute(self, code: str, timeout: int = 300) -> dict[str, Any]:
        """Execute code synchronously (for internal use from main thread).

        Processes the queue directly since we're already on the main thread.
        """
        result_queue: queue.Queue = queue.Queue()
        self._request_queue.put(("direct", code, result_queue))
        self._process_queue()
        return result_queue.get(timeout=timeout)

    def shutdown(self):
        """Stop all threads and close the socket server."""
        self._running = False
        if self._timer:
            self._timer.stop()
        if self.server:
            try:
                self.server.close()
            except OSError:
                pass
