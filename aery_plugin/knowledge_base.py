import json
import os
from typing import Optional


class KnowledgeBase:
    def __init__(self, knowledge_dir: Optional[str] = None):
        self._snippets: list[dict] = []
        self._index: dict[str, set[int]] = {}  # keyword -> snippet indices
        self._load_knowledge(knowledge_dir)
        self._index_snippets()
    
    def _load_knowledge(self, knowledge_dir: Optional[str] = None):
        if knowledge_dir is None:
            # Default to knowledge/ subdirectory of this module
            knowledge_dir = os.path.join(os.path.dirname(__file__), "knowledge")
        if not os.path.isdir(knowledge_dir):
            return
        for filename in os.listdir(knowledge_dir):
            if not filename.endswith(".json"):
                continue
            filepath = os.path.join(knowledge_dir, filename)
            try:
                with open(filepath) as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self._snippets.extend(data)
                    elif isinstance(data, dict):
                        self._snippets.append(data)
            except Exception:
                pass  # Skip malformed files
    
    def _index_snippets(self):
        for i, snippet in enumerate(self._snippets):
            keywords = snippet.get("keywords", [])
            name = snippet.get("name", "")
            desc = snippet.get("description", "")
            sig = snippet.get("signature", "")
            # Index by keywords, name, and description words
            words = set(keywords)
            words.update(name.lower().replace("_", " ").split())
            words.update(desc.lower().replace("(", " ").replace(")", " ").split())
            words.update(sig.lower().replace("(", " ").replace(")", " ").replace(",", " ").split())
            for word in words:
                if len(word) > 2:
                    self._index.setdefault(word, set()).add(i)
    
    def retrieve(self, query: str, top_k: int = 8) -> list[dict]:
        query_words = set(query.lower().replace("_", " ").replace("(", " ").replace(")", " ").split())
        query_words = {w for w in query_words if len(w) > 2}
        
        scores: dict[int, int] = {}
        for word in query_words:
            for idx in self._index.get(word, set()):
                scores[idx] = scores.get(idx, 0) + 1
        
        ranked = sorted(scores.items(), key=lambda x: (-x[1], x[0]))
        return [self._snippets[i] for i, _score in ranked[:top_k]]
    
    def format_for_prompt(self, snippets: list[dict]) -> str:
        lines = []
        for s in snippets:
            sig = s.get("signature", s.get("name", ""))
            desc = s.get("description", "")
            lines.append(f"  - {sig}: {desc}")
        return "\n".join(lines)
    
    def format_and_cap(self, query: str, max_chars: int = 1500) -> str:
        snippets = self.retrieve(query, top_k=20)
        result = self.format_for_prompt(snippets)
        if len(result) > max_chars:
            # Keep as many complete snippets as fit under cap
            kept = []
            total = 0
            for s in snippets:
                sig = s.get("signature", s.get("name", ""))
                desc = s.get("description", "")
                line = f"  - {sig}: {desc}\n"
                if total + len(line) > max_chars:
                    break
                kept.append(line)
                total += len(line)
            result = "".join(kept)
        return result