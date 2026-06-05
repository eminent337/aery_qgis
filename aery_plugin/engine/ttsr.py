import re
from dataclasses import dataclass

@dataclass
class StreamRule:
    pattern: str
    correction: str
