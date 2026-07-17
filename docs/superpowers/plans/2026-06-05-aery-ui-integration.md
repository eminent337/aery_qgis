# Aery Engine UI Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Disconnect the QGIS UI from the legacy `agent.py` and wire it completely to the new `AeryEngine` architecture. 

**Architecture:** We will create an integration layer (`aery_plugin.engine_adapter`) that maps the QGIS GUI inputs (from `chat_panel.py`) directly into the new streaming `AeryEngine`. We will then update the background thread workers to stream tokens using the new engine's TTSR interceptor, and finally deprecate `agent.py`.

**Tech Stack:** Python 3.10+, PyQt6/QGIS APIs.

---

### Task 1: Create Engine Adapter

**Files:**
- Create: `aery_plugin/engine_adapter.py`
- Modify: `aery_plugin/chat_panel.py`

- [ ] **Step 1: Write the Engine Adapter**

```python
# aery_plugin/engine_adapter.py
from .engine.core import AeryEngine
from .engine.tools import EvalTool
from .engine.vision import InspectImageTool
from .engine.memory import HindsightBank
from .engine.ttsr import StreamRule

class AeryEngineAdapter:
    def __init__(self):
        self.engine = AeryEngine()
        self.eval_tool = EvalTool()
        self.vision_tool = InspectImageTool()
        self.memory_bank = HindsightBank()
        
        # Add basic TTSR rules
        self.engine.add_rule(StreamRule(r"os\.system", "Use QgsProcess instead"))
        
    def stream_query(self, query: str):
        # In a real scenario, this would yield from the LLM client.
        # For adapter scaffolding, we yield a dummy response.
        yield f"Processing with AeryEngine: {query}"
```

- [ ] **Step 2: Connect Chat Panel to Adapter**

Modify `aery_plugin/chat_panel.py`. We need to initialize the adapter instead of the legacy `Agent`.
Locate the initialization of the agent.

```python
# aery_plugin/chat_panel.py
# (Inside the AeryChatPanel init or where appropriate)
from .engine_adapter import AeryEngineAdapter

# Replace legacy agent instantiation
# self.agent = Agent(...) -> self.engine = AeryEngineAdapter()
```
*(Note: Real implementation requires careful patching of `chat_panel.py` depending on its exact structure. This task will require reading `chat_panel.py` during execution).*

- [ ] **Step 3: Commit**

```bash
git add aery_plugin/engine_adapter.py aery_plugin/chat_panel.py
git commit -m "feat: wire QGIS chat panel to new AeryEngine"
```

### Task 2: Deprecate Legacy Agent

**Files:**
- Modify: `aery_plugin/agent.py`

- [ ] **Step 1: Mark legacy agent as deprecated**

```python
# Add deprecation warning at the top of aery_plugin/agent.py
import warnings
warnings.warn("agent.py is deprecated. Use AeryEngine in engine/core.py instead.", DeprecationWarning)
```

- [ ] **Step 2: Commit**

```bash
git add aery_plugin/agent.py
git commit -m "chore: deprecate legacy agent.py"
```
