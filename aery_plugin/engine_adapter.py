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
