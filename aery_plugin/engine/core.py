from dataclasses import dataclass
from typing import Generator, List
import re
from .ttsr import StreamRule

@dataclass
class StreamResult:
    text: str
    aborted: bool
    injected_prompt: str = ""

class AeryEngine:
    def __init__(self):
        self.rules: List[StreamRule] = []
        
    def add_rule(self, rule: StreamRule):
        self.rules.append(rule)
        
    def run_stream(self, stream: Generator[str, None, None]) -> StreamResult:
        buffer = ""
        for chunk in stream:
            buffer += chunk
            for rule in self.rules:
                if re.search(rule.pattern, buffer):
                    return StreamResult(
                        text=buffer, 
                        aborted=True, 
                        injected_prompt=rule.correction
                    )
        return StreamResult(text=buffer, aborted=False)
