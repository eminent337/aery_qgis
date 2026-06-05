from .providers_impl import KiloProvider, OpenCodeZenProvider, AntigravityProvider

def create_registry() -> AeryModelRegistry:
    registry = AeryModelRegistry()
    registry.register_provider("kilo", KiloProvider())
    registry.register_provider("opencode-zen", OpenCodeZenProvider())
    registry.register_provider("google-antigravity", AntigravityProvider())
    return registry
