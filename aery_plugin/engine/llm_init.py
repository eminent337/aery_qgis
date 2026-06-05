from PyQt6.QtCore import QSettings
from .providers_impl import KiloProvider, OpenCodeZenProvider, AntigravityProvider, CustomOpenAIProvider
from .llm import AeryModelRegistry

def create_registry() -> AeryModelRegistry:
    settings = QSettings()
    api_key = settings.value("aery/settings/api_key", "")
    base_url = settings.value("aery/settings/base_url", "https://api.openai.com/v1")
    
    registry = AeryModelRegistry()
    registry.register_provider("kilo", KiloProvider(api_key=api_key))
    registry.register_provider("opencode-zen", OpenCodeZenProvider(api_key=api_key))
    registry.register_provider("google-antigravity", AntigravityProvider(api_key=api_key))
    registry.register_provider("custom-openai", CustomOpenAIProvider(base_url=base_url, api_key=api_key))
    
    return registry
