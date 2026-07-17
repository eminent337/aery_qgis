# Aery OMP Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Completely remove the legacy `agent.py`, `llm_client.py`, and `providers.py`, and wire the QGIS ChatPanel directly to the new `AeryEngineAdapter`.

**Architecture:** We will delete the legacy python files. We will expand `AeryEngineAdapter` (which currently only implements `stream_query`) to implement the necessary PyQt signals (`finished`, `error`, `message_ready`, `tool_execution_started`, `tool_execution_finished`) that the UI expects. We will then update `plugin.py` to instantiate `AeryEngineAdapter` instead of `Agent`.

**Tech Stack:** Python 3, PyQt6, QGIS API

---

### Task 1: Delete Legacy Engine Files

**Files:**
- Delete: `aery_plugin/agent.py`
- Delete: `aery_plugin/llm_client.py`
- Delete: `aery_plugin/providers.py`

- [ ] **Step 1: Delete the files**

```bash
git rm aery_plugin/agent.py
git rm aery_plugin/llm_client.py
git rm aery_plugin/providers.py
```

- [ ] **Step 2: Commit**

```bash
git commit -m "refactor: permanently delete legacy agent and llm clients"
```

### Task 2: Implement Signals in AeryEngineAdapter

**Files:**
- Modify: `aery_plugin/engine_adapter.py`

- [ ] **Step 1: Add PyQt6 QObject inheritance and define signals**

```python
from PyQt6.QtCore import QObject, pyqtSignal
from .engine.core import AeryEngine
from .engine.tools import EvalTool
from .engine.vision import InspectImageTool
from .engine.memory import HindsightBank
from .engine.ttsr import StreamRule
from .engine.llm_init import create_registry

class AeryEngineAdapter(QObject):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)
    message_ready = pyqtSignal(str, str)
    tool_execution_started = pyqtSignal(str, str, str)
    tool_execution_finished = pyqtSignal(str, str, str, str)
    
    def __init__(self):
        super().__init__()
        self.engine = AeryEngine()
        self.eval_tool = EvalTool()
        self.vision_tool = InspectImageTool()
        self.memory_bank = HindsightBank()
        self.llm_registry = create_registry()
        
        self.engine.add_rule(StreamRule(r"os\.system", "Use QgsProcess instead"))
        
    async def stream_query(self, query: str):
        # existing implementation
        from PyQt6.QtCore import QSettings
        settings = QSettings()
        provider_id = settings.value("aery/settings/provider", "opencode-zen")
        self.llm_registry = create_registry()
        provider = self.llm_registry.get_provider(provider_id)
        messages = [{"role": "user", "content": query}]
        async for chunk in provider.stream_chat(messages, provider_id):
            yield chunk

    def run_agent(self, context: dict, prompt: str):
        # Mock implementation for now to satisfy UI before full async threading is built
        self.message_ready.emit("assistant", "Engine adapter online. Full TTSR stream loop pending implementation.")
        self.finished.emit({"status": "success", "messages": []})
        
    def stop_execution(self):
        pass
```

- [ ] **Step 2: Run tests to verify syntax**

Run: `python -m py_compile aery_plugin/engine_adapter.py`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add aery_plugin/engine_adapter.py
git commit -m "feat: add PyQt signals to AeryEngineAdapter"
```

### Task 3: Wire Plugin and ChatPanel to EngineAdapter

**Files:**
- Modify: `aery_plugin/plugin.py`
- Modify: `aery_plugin/chat_panel.py`

- [ ] **Step 1: Modify plugin.py to import AeryEngineAdapter instead of Agent**

Replace `from aery_plugin.agent import Agent` with `from aery_plugin.engine_adapter import AeryEngineAdapter`.
Change `self.agent: Optional[Agent] = None` to `self.agent: Optional[AeryEngineAdapter] = None`.
Change `self.agent = Agent(executor=self.executor, iface=self.iface)` to `self.agent = AeryEngineAdapter()`.

- [ ] **Step 2: Clean up ChatPanel references if needed**

Ensure `chat_panel.py` uses the signals from `self.agent` natively (which it does via `self.agent.finished.connect`).

- [ ] **Step 3: Test plugin loads in QGIS**

Run QGIS unit tests or `pytest` to ensure `ModuleNotFoundError` is fully resolved and UI can instantiate.

- [ ] **Step 4: Commit**

```bash
git add aery_plugin/plugin.py aery_plugin/chat_panel.py
git commit -m "refactor: wire plugin to new AeryEngineAdapter"
```
