from .engine.core import AeryEngine
from .engine.tools import EvalTool
from .engine.vision import InspectImageTool
from .engine.memory import HindsightBank
from .engine.ttsr import StreamRule
from .engine.llm_init import create_registry

class AeryEngineAdapter:
    def __init__(self):
        self.engine = AeryEngine()
        self.eval_tool = EvalTool()
        self.vision_tool = InspectImageTool()
        self.memory_bank = HindsightBank()
        self.llm_registry = create_registry()
        
        # Add basic TTSR rules
        self.engine.add_rule(StreamRule(r"os\.system", "Use QgsProcess instead"))
        
    async def stream_query(self, query: str):
        from PyQt6.QtCore import QSettings
        settings = QSettings()
        provider_id = settings.value("aery/settings/provider", "opencode-zen")
        
        # Re-create registry so it catches any newly saved settings
        self.llm_registry = create_registry()
        provider = self.llm_registry.get_provider(provider_id)
        
        messages = [{"role": "user", "content": query}]
        
        async for chunk in provider.stream_chat(messages, provider_id):
            yield chunk
