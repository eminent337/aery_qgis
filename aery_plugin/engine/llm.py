import abc
from typing import AsyncGenerator, Dict, Type

class ProviderBase(abc.ABC):
    @abc.abstractmethod
    async def stream_chat(self, messages: list[dict], model: str) -> AsyncGenerator[str, None]:
        pass

class AeryModelRegistry:
    def __init__(self):
        self._providers: Dict[str, ProviderBase] = {}

    def register_provider(self, name: str, provider: ProviderBase):
        self._providers[name] = provider

    def get_provider(self, name: str) -> ProviderBase:
        if name not in self._providers:
            raise ValueError(f"Provider {name} not found")
        return self._providers[name]
