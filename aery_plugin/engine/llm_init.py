from .providers_impl import KiloProvider, OpenCodeZenProvider, AntigravityProvider, CustomOpenAIProvider
from .llm import AeryModelRegistry
from aery_plugin.oauth_helper import _load_auth, API_PROVIDERS, get_custom_providers

def create_registry() -> AeryModelRegistry:
    registry = AeryModelRegistry()
    auth = _load_auth()
    
    kilo_creds = auth.get("kilo", {})
    kilo_key = kilo_creds.get("access", "")
    registry.register_provider("kilo", KiloProvider(api_key=kilo_key))
    
    opencode_creds = auth.get("opencode", {})
    opencode_key = opencode_creds.get("access", "")
    registry.register_provider("opencode-zen", OpenCodeZenProvider(api_key=opencode_key))
    
    ag_creds = auth.get("google-antigravity", {})
    ag_key = ag_creds.get("access", "")
    registry.register_provider("google-antigravity", AntigravityProvider(api_key=ag_key))
    
    # Custom providers
    customs = get_custom_providers()
    for custom in customs:
        pid = custom["id"]
        creds = auth.get(pid, {})
        api_key = creds.get("access", "")
        base_url = custom.get("base_url", "https://api.openai.com/v1")
        registry.register_provider(pid, CustomOpenAIProvider(base_url=base_url, api_key=api_key))
            
    # Default custom openai fallback if no custom providers exist
    registry.register_provider("custom-openai", CustomOpenAIProvider(base_url="https://api.openai.com/v1", api_key=""))
    
    return registry

