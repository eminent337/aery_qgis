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
        # We will default to opencode-zen for standard free usage
        provider = self.llm_registry.get_provider("opencode-zen")
        messages = [{"role": "user", "content": query}]
        
        async for chunk in provider.stream_chat(messages, "opencode-zen"):
            yield chunk
