class HindsightBank:
    def __init__(self):
        self.memories = []
        
    def retain(self, fact: str):
        self.memories.append(fact)
        
    def recall(self, query: str) -> list[str]:
        return [m for m in self.memories if query.lower() in m.lower()]
