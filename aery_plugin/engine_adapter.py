from PyQt6.QtCore import QObject, pyqtSignal, QThread
from .engine.core import AeryEngine
from .engine.tools import EvalTool, EditTool
from .engine.vision import InspectImageTool
from .engine.memory import HindsightBank
from .engine.ttsr import StreamRule
from .engine.llm_init import create_registry
import asyncio
import json

class EngineWorker(QThread):
    chunk = pyqtSignal(dict)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    
    def __init__(self, query: str, context: dict, llm_registry, engine, tools):
        super().__init__()
        self.query = query
        self.context = context
        self.llm_registry = llm_registry
        self.engine = engine
        self.tools = tools
        
    def run(self):
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(self._run_async())
        except Exception as e:
            msg = str(e)
            if "402" in msg:
                msg = (
                    f"HTTP Error 402 (Payment Required): Your active LLM provider or API key has run out of credits/quota. "
                    f"Please check your account billing or select/configure a different model/provider in the Aery settings.\n\n"
                    f"Details from Server:\n{msg}"
                )
            self.error.emit(msg)
            
    async def _run_async(self):
        from aery_plugin.oauth_helper import get_active_provider
        active = get_active_provider()
        provider_id = active["id"] if active else "opencode-zen"

        provider = self.llm_registry.get_provider(provider_id)
        
        messages = [{"role": "user", "content": self.query}]
        if "vision" in self.context and self.context["vision"]:
            messages[0]["content"] = [
                {"type": "text", "text": self.query},
                self.context["vision"]
            ]
        
        # Simulated core streaming loop with TTSR interception
        # Resolve the model the user actually selected; Kilo now 402s on any
        # non-free model, so force a known-working free model if needed.
        model = active.get("model", "") if active else ""
        if provider_id == "kilo" and model and ":free" not in model and model != "openrouter/free":
            model = "stepfun/step-3.7-flash:free"

        stream = provider.stream_chat(messages, model)
        
        buffer = ""
        aborted = False
        async for text_chunk in stream:
            # Emit in legacy format
            self.chunk.emit({"type": "stream_event", "event": {"type": "text", "text": text_chunk}})
            buffer += text_chunk
            
            # TTSR Check
            for rule in self.engine.rules:
                import re
                if re.search(rule.pattern, buffer):
                    self.chunk.emit({"type": "system", "subtype": "ttsr", "message": f"Stream Rule Triggered: {rule.correction}"})
                    aborted = True
                    break
            if aborted:
                break
                
        # For this execution, we'll route mock execution logic into our new tools
        if "```python" in buffer and "eval" in buffer.lower():
            code_block = buffer.split("```python")[1].split("```")[0].strip()
            self.chunk.emit({"type": "tool_start", "tool": "eval", "description": "Evaluating Python Code"})
            result = self.tools['eval'].execute(code_block)
            self.chunk.emit({"type": "tool_use_summary", "summary": result})
            
        elif "capture_canvas" in buffer:
            self.chunk.emit({"type": "tool_start", "tool": "capture_canvas", "description": "Taking Screenshot"})
            result = self.tools['vision'].execute("capture_canvas")
            self.chunk.emit({"type": "tool_use_summary", "summary": "Canvas captured successfully."})
            
        self.finished.emit("Done")


class AeryEngineAdapter(QObject):
    chunk = pyqtSignal(dict)
    finished = pyqtSignal(str)
    error = pyqtSignal(str)
    
    def __init__(self):
        super().__init__()
        self.engine = AeryEngine()
        self.tools = {
            "eval": EvalTool(),
            "edit": EditTool(),
            "vision": InspectImageTool(),
            "memory": HindsightBank()
        }
        self.llm_registry = create_registry()
        
        # Core OMP Rules
        self.engine.add_rule(StreamRule(r"os\.system", "Use QgsProcess instead"))
        self.engine.add_rule(StreamRule(r"subprocess\.Popen", "Use QgsProcess instead"))
        
        self._worker = EngineWorker("", {}, self.llm_registry, self.engine, self.tools)
        
    def start(self, prompt: str, context: dict = None):
        if self._worker and self._worker.isRunning():
            return
            
        if context is None:
            context = {}
            
        # Ensure registry is up to date with user API keys
        self.llm_registry = create_registry()
            
        # Create fresh worker and wire signals
        if self._worker:
            self._worker.deleteLater()
            
        self._worker = EngineWorker(prompt, context, self.llm_registry, self.engine, self.tools)
        
        # Map worker signals to legacy interface
        self._worker.chunk.connect(self.chunk.emit)
        self._worker.finished.connect(self.finished.emit)
        self._worker.error.connect(self.error.emit)
        
        self._worker.start()
        
    def stop_execution(self):
        if self._worker and self._worker.isRunning():
            self._worker.terminate()
            self._worker.wait()
    def cancel(self) -> None:
        """Cancel the current execution."""
        self.stop_execution()
    def reset(self) -> None:
        """Reset agent state."""
        pass
    def reinitialize(self) -> None:
        """Reinitialize the LLM client registry with fresh configuration."""
        self.llm_registry = create_registry()
    def resolve_permission(self, request_id: str, approved: bool, always: bool = False) -> None:
        """Resolve a pending permission request."""
        pass
    def invalidate_project_context(self) -> None:
        """Invalidate the cached project snapshot after QGIS state changes."""
        pass
