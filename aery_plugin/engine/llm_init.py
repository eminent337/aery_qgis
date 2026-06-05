from .providers_impl import KiloProvider, OpenCodeZenProvider, AntigravityProvider, CustomOpenAIProvider

def create_registry() -> AeryModelRegistry:
    registry = AeryModelRegistry()
    registry.register_provider("kilo", KiloProvider())
    registry.register_provider("opencode-zen", OpenCodeZenProvider())
    registry.register_provider("google-antigravity", AntigravityProvider())
    
    # Register a default custom openai compatible endpoint (base_url usually loaded from config)
    registry.register_provider("custom-openai", CustomOpenAIProvider(base_url="https://api.openai.com/v1"))
    
    return registry
