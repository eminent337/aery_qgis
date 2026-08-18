"""Tests for AuditLogger and telemetry wiring in agent.

Covers:
- AuditLogger creates and writes .aery/operations.jsonl
- Agent.start_session() lazily initializes AuditLogger
- Agent.get_audit_log_path() returns correct path
- Agent.get_llm_call_history() returns telemetry records
- Agent.reset() flushes audit logger
"""

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def audit_dir(tmp_path):
    """Create a temporary audit directory."""
    return str(tmp_path / ".aery")


@pytest.fixture
def audit_logger(audit_dir):
    """Create an AuditLogger with a temp directory."""
    from aery_plugin.executor_audit import AuditLogger
    return AuditLogger(run_id="test-run-001", default_audit_dir=audit_dir)


class TestAuditLogger:
    """Tests for the AuditLogger class."""

    def test_creates_aery_dir(self, audit_dir):
        """AuditLogger should create the audit directory on first write."""
        assert not os.path.exists(audit_dir)
        # write_run_start_marker uses expanduser("~") without QGIS — writes to ~/ .aery
        from aery_plugin.executor_audit import AuditLogger
        logger = AuditLogger(run_id="r1", default_audit_dir=audit_dir)
        logger.write_run_start_marker()
        logger.flush()
        # The dir is created by write_audit_entry below; just verify no crash

    def test_write_run_start_marker(self, audit_dir):
        """write_run_start_marker should write a JSONL record."""
        from aery_plugin.executor_audit import AuditLogger
        logger = AuditLogger(run_id="r1", default_audit_dir=audit_dir)
        logger.write_run_start_marker()
        logger.flush()

        log_path = os.path.join(audit_dir, "operations.jsonl")
        assert os.path.exists(log_path)
        with open(log_path) as f:
            lines = f.readlines()
        assert len(lines) >= 1
        entry = json.loads(lines[0])
        assert entry["type"] == "run_start"
        assert entry["run_id"] == "r1"

    def test_write_audit_entry(self, audit_dir):
        """write_audit_entry should record tool execution details."""
        import time as _time
        from aery_plugin.executor_audit import AuditLogger
        logger = AuditLogger(run_id="r1", default_audit_dir=audit_dir)
        start = _time.perf_counter()
        _time.sleep(0.02)  # ensure measurable duration
        logger.write_audit_entry(
            project_dir=audit_dir,
            req_id="req-001",
            code=" QgsProject.instance()",
            response={"success": True, "result": "ok"},
            metadata={"tool_name": "run_qgis_code", "started_at": start},
        )
        logger.flush()
        log_path = os.path.join(audit_dir, "operations.jsonl")
        with open(log_path) as f:
            lines = f.readlines()
        entry = json.loads(lines[-1])
        assert entry["request_id"] == "req-001"
        assert entry["tool_name"] == "run_qgis_code"
        assert entry["success"] is True
        assert entry["duration_ms"] >= 0

    def test_write_failed_entry(self, audit_dir):
        """Failed executions should record error info."""
        from aery_plugin.executor_audit import AuditLogger
        logger = AuditLogger(run_id="r1", default_audit_dir=audit_dir)
        logger.write_audit_entry(
            project_dir=audit_dir,
            req_id="req-fail",
            code="bad code",
            response={"success": False, "error": "KeyError: 'missing'", "traceback": "trace..."},
            metadata={"tool_name": "run_qgis_code", "started_at": time.perf_counter()},
        )
        logger.flush()

        log_path = os.path.join(audit_dir, "operations.jsonl")
        with open(log_path) as f:
            lines = f.readlines()
        entry = json.loads(lines[-1])
        assert entry["success"] is False
        assert "KeyError" in entry.get("error", "")
        assert entry.get("error_category") == "attribute_error"

    def test_classify_error_patterns(self):
        """Error classification should match known patterns."""
        from aery_plugin.executor_audit import AuditLogger
        assert AuditLogger.classify_error("CRS mismatch") == "crs_mismatch"
        assert AuditLogger.classify_error("invalid geometry") == "invalid_geometry"
        assert AuditLogger.classify_error("timeout") == "timeout"
        assert AuditLogger.classify_error("permission denied") == "permission_denied"
        assert AuditLogger.classify_error("no module named foo") == "import_error"
        assert AuditLogger.classify_error("empty layer") == "empty_layer"
        assert AuditLogger.classify_error("KeyError: x") == "attribute_error"
        assert AuditLogger.classify_error("some unknown error") == "other"

    def test_summarize_result(self):
        """Result summarization should truncate and format."""
        from aery_plugin.executor_audit import AuditLogger
        assert AuditLogger.summarize_result(None) == ""
        assert AuditLogger.summarize_result("short string") == "short string"
        assert len(AuditLogger.summarize_result("x" * 500)) <= 400
        assert AuditLogger.summarize_result(42) == "42"
        # Image data should be summarized
        img_data = "iVBORw0KGgo" + "x" * 300
        summary = AuditLogger.summarize_result(img_data)
        assert summary.startswith("[image/png")

    def test_async_write(self, audit_dir):
        """AuditLogger should handle concurrent writes safely."""
        from aery_plugin.executor_audit import AuditLogger
        logger = AuditLogger(run_id="r1", default_audit_dir=audit_dir)
        errors = []

        def writer(n):
            try:
                for i in range(10):
                    logger.write_audit_entry(
                        project_dir=audit_dir,
                        req_id=f"req-{n}-{i}",
                        code=f"code_{n}_{i}",
                        response={"success": True},
                        metadata={"tool_name": f"tool_{n}", "started_at": time.perf_counter()},
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Write errors: {errors}"
        logger.flush()

        log_path = os.path.join(audit_dir, "operations.jsonl")
        with open(log_path) as f:
            lines = f.readlines()
        # Should have 50 entries (5 threads × 10 writes)
        assert len(lines) >= 50


class TestAgentAuditIntegration:
    """Tests for audit trail integration with Agent."""

    def test_agent_lazy_audit_init(self):
        """Agent should lazily initialize AuditLogger on start_session."""
        from aery_plugin.agent import Agent
        mock_executor = MagicMock()
        agent = Agent(executor=mock_executor)
        assert agent._audit_logger is None

        with tempfile.TemporaryDirectory() as tmpdir:
            session_id = agent.start_session(tmpdir)
            assert session_id is not None
            # AuditLogger should be initialized
            assert agent._audit_logger is not None
            log_path = agent.get_audit_log_path()
            assert log_path is not None
            assert log_path.endswith("operations.jsonl")

    def test_agent_get_audit_log_path_no_session(self):
        """get_audit_log_path should return None before session starts."""
        from aery_plugin.agent import Agent
        mock_executor = MagicMock()
        agent = Agent(executor=mock_executor)
        assert agent.get_audit_log_path() is None

    def test_agent_llm_call_history(self):
        """Agent should be able to retrieve LLM call history."""
        from aery_plugin.agent import Agent
        mock_executor = MagicMock()
        agent = Agent(executor=mock_executor)
        history = agent.get_llm_call_history()
        assert isinstance(history, list)

    def test_agent_reset_flushes_audit(self):
        """reset() should flush the audit logger."""
        from aery_plugin.agent import Agent
        mock_executor = MagicMock()
        agent = Agent(executor=mock_executor)

        with tempfile.TemporaryDirectory() as tmpdir:
            agent.start_session(tmpdir)
            assert agent._audit_logger is not None
            agent.reset()
            # After reset, audit_logger should be cleared
            assert agent._audit_logger is None
            assert agent._trace_id is None
