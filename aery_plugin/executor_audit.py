import os
import json
import time
from datetime import datetime, timezone
from typing import Any

from aery_plugin.logger import logger

class AuditLogger:
    """Handles logging agent operations to an audit trail (.aery/operations.jsonl)."""

    def __init__(self, run_id: str, default_audit_dir: str = None):
        self.run_id = run_id
        self.default_audit_dir = default_audit_dir
        self._queue = __import__('queue').Queue()
        self._thread = __import__('threading').Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self):
        import os, json
        while True:
            try:
                audit_dir, entry = self._queue.get()
                if audit_dir is None:
                    self._queue.task_done()
                    break
                os.makedirs(audit_dir, exist_ok=True)
                with open(os.path.join(audit_dir, "operations.jsonl"), "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
                self._queue.task_done()
            except Exception as e:
                logger.debug("executor_audit: worker failed to write audit entry: %s", e)

    def flush(self):
        """Block until all queued writes have been flushed to disk."""
        self._queue.join()

    def get_audit_dir(self, project_dir: str) -> str:
        return self.default_audit_dir or os.path.join(project_dir, ".aery")

    def append_audit_record(self, audit_dir: str, entry: dict[str, Any]) -> None:
        self._queue.put((audit_dir, entry))

    def write_run_start_marker(self) -> None:
        try:
            project_dir = os.path.expanduser("~")
            try:
                from qgis.core import QgsProject
                project_path = QgsProject.instance().fileName() or ""
                if project_path:
                    project_dir = os.path.dirname(project_path)
            except Exception as e:
                logger.debug("executor_audit: failed to get project path in write_run_start_marker: %s", e)
                project_path = ""
            self.append_audit_record(self.get_audit_dir(project_dir), {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "run_start",
                "run_id": self.run_id,
                "source": "plugin",
                "project_dir": project_dir,
            })
        except Exception as e:
            logger.debug("executor_audit: write_run_start_marker failed: %s", e)

    def write_audit_entry(self, project_dir: str, req_id: str, code: str, response: dict, metadata: dict) -> None:
        try:
            audit_dir = self.get_audit_dir(project_dir)
            metadata = metadata or {}
            try:
                from qgis.core import QgsProject
                project_path = QgsProject.instance().fileName() or ""
            except Exception as e:
                logger.debug("executor_audit: failed to get project path in write_audit_entry: %s", e)
                project_path = ""
            response = response or {}
            duration_ms = int((time.perf_counter() - metadata.get("started_at", time.perf_counter())) * 1000)
            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "request_id": req_id,
                "tool_name": metadata.get("tool_name") or "run_code",
                "run_id": metadata.get("run_id", self.run_id),
                "source": metadata.get("source", "plugin"),
                "phase": "end",
                "success": bool(response.get("success")),
                "duration_ms": duration_ms,
                "project_path": project_path,
                "project_dir": project_dir,
                "code": code if code != "__capture_canvas__" else "[canvas capture]",
                "result_summary": self.summarize_result(response.get("result")),
                "risks": response.get("risks", []),
            }
            if not response.get("success"):
                entry["error"] = response.get("error", "")
                entry["traceback"] = response.get("traceback", "")
                entry["error_category"] = self.classify_error(response.get("error", ""))
            self.append_audit_record(audit_dir, entry)
        except Exception as e:
            logger.debug("executor_audit: write_audit_entry failed: %s", e)

    @staticmethod
    def classify_error(error_str: str) -> str:
        """Classify an error string into a broad category for analytics."""
        s = error_str.lower()
        if "crs" in s or "coordinate" in s or "reproject" in s:
            return "crs_mismatch"
        if "invalid geometry" in s or "fixgeometries" in s:
            return "invalid_geometry"
        if "timeout" in s or "timed out" in s:
            return "timeout"
        if "permission" in s or "permissionerror" in s or "access denied" in s:
            return "permission_denied"
        if "no module named" in s or "importerror" in s or "modulenotfounderror" in s:
            return "import_error"
        if "featurecount" in s or "empty layer" in s or "no features" in s:
            return "empty_layer"
        if "keyerror" in s or "indexerror" in s or "attributeerror" in s:
            return "attribute_error"
        return "other"

    @staticmethod
    def summarize_result(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            s = value.strip()
            if s.startswith("iVBORw0KGgo") and len(s) > 256:
                return f"[image/png base64, {len(s)} chars]"
            return s[:400]
        if isinstance(value, (int, float, bool)):
            return str(value)[:400]
        try:
            return json.dumps(value, ensure_ascii=False)[:400]
        except TypeError:
            return str(value)[:400]
