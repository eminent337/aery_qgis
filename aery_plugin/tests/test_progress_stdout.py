"""Tests for ProgressStdout tee in executor."""

import io
import threading
from aery_plugin.qgis_executor import _ProgressStdout


def test_progress_stdout_captures_lines():
    real = io.StringIO()
    emitted = []
    tee = _ProgressStdout(real, lambda line: emitted.append(line), threading.current_thread())
    tee.write("hello\nworld\n")
    assert real.getvalue() == "hello\nworld\n"
    assert emitted == ["hello", "world"]


def test_progress_stdout_flush_partial_line():
    real = io.StringIO()
    emitted = []
    tee = _ProgressStdout(real, lambda line: emitted.append(line), threading.current_thread())
    tee.write("partial")
    assert emitted == []
    tee.flush()
    assert emitted == ["partial"]


def test_progress_stdout_ignores_other_threads():
    real = io.StringIO()
    emitted = []
    owner = threading.current_thread()
    tee = _ProgressStdout(real, lambda line: emitted.append(line), owner)

    def other():
        tee.write("from_other_thread\n")

    t = threading.Thread(target=other)
    t.start()
    t.join()

    # Pass-through happened, but no progress emission
    assert "from_other_thread\n" in real.getvalue()
    assert emitted == []
