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
        from PyQt6.QtCore import QSettings
        settings = QSettings()
        provider_id = settings.value("aery/settings/provider", "opencode-zen")
        self.llm_registry = create_registry()
        provider = self.llm_registry.get_provider(provider_id)
        messages = [{"role": "user", "content": query}]
        async for chunk in provider.stream_chat(messages, provider_id):
            yield chunk

    def run_agent(self, context: dict, prompt: str):
        self.message_ready.emit("assistant", "Engine adapter online. Full TTSR stream loop pending implementation.")
        self.finished.emit({"status": "success", "messages": []})
        
    def stop_execution(self):
        pass
