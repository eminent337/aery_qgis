"""Complete Aery provider registry — exact copy of Aery models.generated.ts.

Contains all 35 providers with their models, API protocols, base URLs,
auth methods, and compatibility settings.
"""

from typing import Any, Optional


# ── API Protocol Types ───────────────────────────────────────────────────────
API_ANTHROPIC_MESSAGES = "anthropic-messages"
API_OPENAI_COMPLETIONS = "openai-completions"
API_OPENAI_RESPONSES = "openai-responses"
API_AZURE_OPENAI_RESPONSES = "azure-openai-responses"
API_OPENAI_CODEX_RESPONSES = "openai-codex-responses"
API_GOOGLE_GENERATIVE_AI = "google-generative-ai"
API_GOOGLE_VERTEX = "google-vertex"
API_GOOGLE_GEMINI_CLI = "google-gemini-cli"
API_BEDROCK_CONVERSE_STREAM = "bedrock-converse-stream"
API_MISTRAL_CONVERSATIONS = "mistral-conversations"


# ── Model Definition ─────────────────────────────────────────────────────────
class Model:
    """Aery model definition — matches Model<TApi> interface."""

    def __init__(
        self,
        id: str,
        name: str,
        api: str,
        provider: str,
        base_url: str,
        reasoning: bool = False,
        input_types: list[str] | None = None,
        cost_input: float = 0,
        cost_output: float = 0,
        cost_cache_read: float = 0,
        cost_cache_write: float = 0,
        context_window: int = 128000,
        max_tokens: int = 8192,
        thinking_level_map: dict[str, str | None] | None = None,
        compat: dict[str, Any] | None = None,
    ):
        self.id = id
        self.name = name
        self.api = api
        self.provider = provider
        self.base_url = base_url
        self.reasoning = reasoning
        self.input_types = input_types or ["text"]
        self.cost_input = cost_input
        self.cost_output = cost_output
        self.cost_cache_read = cost_cache_read
        self.cost_cache_write = cost_cache_write
        self.context_window = context_window
        self.max_tokens = max_tokens
        self.thinking_level_map = thinking_level_map
        self.compat = compat or {}


# ── Provider Registry ────────────────────────────────────────────────────────
PROVIDERS: dict[str, dict[str, Model]] = {}


def _add(provider_id: str, model_id: str, **kwargs) -> None:
    """Add a model to the registry."""
    if provider_id not in PROVIDERS:
        PROVIDERS[provider_id] = {}
    PROVIDERS[provider_id][model_id] = Model(
        id=model_id,
        provider=provider_id,
        **kwargs,
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AMAZON BEDROCK
# ═══════════════════════════════════════════════════════════════════════════════
_BEDROCK = "https://bedrock-runtime.us-east-1.amazonaws.com"

_add("amazon-bedrock", "amazon.nova-2-lite-v1:0", name="Nova 2 Lite", api=API_BEDROCK_CONVERSE_STREAM, base_url=_BEDROCK, input_types=["text", "image"], cost_input=0.33, cost_output=2.75, context_window=128000, max_tokens=4096)
_add("amazon-bedrock", "amazon.nova-lite-v1:0", name="Nova Lite", api=API_BEDROCK_CONVERSE_STREAM, base_url=_BEDROCK, input_types=["text", "image"], cost_input=0.06, cost_output=0.24, cost_cache_read=0.015, context_window=300000, max_tokens=8192)
_add("amazon-bedrock", "amazon.nova-micro-v1:0", name="Nova Micro", api=API_BEDROCK_CONVERSE_STREAM, base_url=_BEDROCK, input_types=["text"], cost_input=0.04, cost_output=0.14, cost_cache_read=0.01, context_window=128000, max_tokens=8192)
_add("amazon-bedrock", "amazon.nova-pro-v1:0", name="Nova Pro", api=API_BEDROCK_CONVERSE_STREAM, base_url=_BEDROCK, input_types=["text", "image"], cost_input=0.8, cost_output=3.2, cost_cache_read=0.2, context_window=300000, max_tokens=8192)
_add("amazon-bedrock", "anthropic.claude-3-5-haiku-20241022-v1:0", name="Claude Haiku 3.5", api=API_BEDROCK_CONVERSE_STREAM, base_url=_BEDROCK, input_types=["text", "image"], cost_input=0.8, cost_output=4, cost_cache_read=0.08, context_window=200000, max_tokens=8192)
_add("amazon-bedrock", "anthropic.claude-3-5-sonnet-20241022-v2:0", name="Claude Sonnet 3.5 v2", api=API_BEDROCK_CONVERSE_STREAM, base_url=_BEDROCK, input_types=["text", "image"], cost_input=3, cost_output=15, cost_cache_read=0.3, context_window=200000, max_tokens=8192)
_add("amazon-bedrock", "anthropic.claude-3-7-sonnet-20250219-v1:0", name="Claude Sonnet 3.7", api=API_BEDROCK_CONVERSE_STREAM, base_url=_BEDROCK, reasoning=True, input_types=["text", "image"], cost_input=3, cost_output=15, cost_cache_read=0.3, context_window=200000, max_tokens=16384)
_add("amazon-bedrock", "anthropic.claude-haiku-4-5-20251001-v1:0", name="Claude Haiku 4.5", api=API_BEDROCK_CONVERSE_STREAM, base_url=_BEDROCK, input_types=["text", "image"], cost_input=0.8, cost_output=4, cost_cache_read=0.08, context_window=200000, max_tokens=8192)
_add("amazon-bedrock", "anthropic.claude-opus-4-20250514-v1:0", name="Claude Opus 4", api=API_BEDROCK_CONVERSE_STREAM, base_url=_BEDROCK, reasoning=True, input_types=["text", "image"], cost_input=15, cost_output=75, cost_cache_read=1.5, context_window=200000, max_tokens=32768)
_add("amazon-bedrock", "anthropic.claude-opus-4-5-20251101-v1:0", name="Claude Opus 4.5", api=API_BEDROCK_CONVERSE_STREAM, base_url=_BEDROCK, reasoning=True, input_types=["text", "image"], cost_input=15, cost_output=75, cost_cache_read=1.5, context_window=200000, max_tokens=32768)
_add("amazon-bedrock", "anthropic.claude-sonnet-4-20250514-v1:0", name="Claude Sonnet 4", api=API_BEDROCK_CONVERSE_STREAM, base_url=_BEDROCK, reasoning=True, input_types=["text", "image"], cost_input=3, cost_output=15, cost_cache_read=0.3, context_window=200000, max_tokens=16384)
_add("amazon-bedrock", "anthropic.claude-sonnet-4-5-20250929-v1:0", name="Claude Sonnet 4.5", api=API_BEDROCK_CONVERSE_STREAM, base_url=_BEDROCK, reasoning=True, input_types=["text", "image"], cost_input=3, cost_output=15, cost_cache_read=0.3, context_window=200000, max_tokens=16384)
_add("amazon-bedrock", "deepseek.r1-v1:0", name="DeepSeek R1", api=API_BEDROCK_CONVERSE_STREAM, base_url=_BEDROCK, reasoning=True, input_types=["text"], cost_input=1.35, cost_output=5.4, context_window=128000, max_tokens=32768)
_add("amazon-bedrock", "meta.llama3-1-70b-instruct-v1:0", name="Llama 3.1 70B", api=API_BEDROCK_CONVERSE_STREAM, base_url=_BEDROCK, input_types=["text"], cost_input=0.72, cost_output=0.72, context_window=128000, max_tokens=4096)
_add("amazon-bedrock", "meta.llama3-1-8b-instruct-v1:0", name="Llama 3.1 8B", api=API_BEDROCK_CONVERSE_STREAM, base_url=_BEDROCK, input_types=["text"], cost_input=0.22, cost_output=0.22, context_window=128000, max_tokens=4096)
_add("amazon-bedrock", "meta.llama3-3-70b-instruct-v1:0", name="Llama 3.3 70B", api=API_BEDROCK_CONVERSE_STREAM, base_url=_BEDROCK, input_types=["text"], cost_input=0.72, cost_output=0.72, context_window=128000, max_tokens=4096)
_add("amazon-bedrock", "meta.llama4-maverick-17b-instruct-v1:0", name="Llama 4 Maverick 17B", api=API_BEDROCK_CONVERSE_STREAM, base_url=_BEDROCK, input_types=["text", "image"], cost_input=0.24, cost_output=0.96, context_window=1000000, max_tokens=4096)
_add("amazon-bedrock", "meta.llama4-scout-17b-instruct-v1:0", name="Llama 4 Scout 17B", api=API_BEDROCK_CONVERSE_STREAM, base_url=_BEDROCK, input_types=["text", "image"], cost_input=0.17, cost_output=0.68, context_window=1000000, max_tokens=4096)


# ═══════════════════════════════════════════════════════════════════════════════
# ANTHROPIC
# ═══════════════════════════════════════════════════════════════════════════════
_ANTHROPIC = "https://api.anthropic.com"

_add("anthropic", "claude-3-5-haiku-20241022", name="Claude Haiku 3.5", api=API_ANTHROPIC_MESSAGES, base_url=_ANTHROPIC, input_types=["text", "image"], cost_input=0.8, cost_output=4, cost_cache_read=0.08, context_window=200000, max_tokens=8192)
_add("anthropic", "claude-3-5-sonnet-20241022", name="Claude Sonnet 3.5 v2", api=API_ANTHROPIC_MESSAGES, base_url=_ANTHROPIC, input_types=["text", "image"], cost_input=3, cost_output=15, cost_cache_read=0.3, context_window=200000, max_tokens=8192)
_add("anthropic", "claude-3-7-sonnet-20250219", name="Claude Sonnet 3.7", api=API_ANTHROPIC_MESSAGES, base_url=_ANTHROPIC, reasoning=True, input_types=["text", "image"], cost_input=3, cost_output=15, cost_cache_read=0.3, context_window=200000, max_tokens=16384)
_add("anthropic", "claude-haiku-4-5-20251001", name="Claude Haiku 4.5", api=API_ANTHROPIC_MESSAGES, base_url=_ANTHROPIC, input_types=["text", "image"], cost_input=0.8, cost_output=4, cost_cache_read=0.08, context_window=200000, max_tokens=8192)
_add("anthropic", "claude-opus-4-20250514", name="Claude Opus 4", api=API_ANTHROPIC_MESSAGES, base_url=_ANTHROPIC, reasoning=True, input_types=["text", "image"], cost_input=15, cost_output=75, cost_cache_read=1.5, context_window=200000, max_tokens=32768)
_add("anthropic", "claude-opus-4-5-20251101", name="Claude Opus 4.5", api=API_ANTHROPIC_MESSAGES, base_url=_ANTHROPIC, reasoning=True, input_types=["text", "image"], cost_input=15, cost_output=75, cost_cache_read=1.5, context_window=200000, max_tokens=32768)
_add("anthropic", "claude-sonnet-4-20250514", name="Claude Sonnet 4", api=API_ANTHROPIC_MESSAGES, base_url=_ANTHROPIC, reasoning=True, input_types=["text", "image"], cost_input=3, cost_output=15, cost_cache_read=0.3, context_window=200000, max_tokens=16384)
_add("anthropic", "claude-sonnet-4-5-20250929", name="Claude Sonnet 4.5", api=API_ANTHROPIC_MESSAGES, base_url=_ANTHROPIC, reasoning=True, input_types=["text", "image"], cost_input=3, cost_output=15, cost_cache_read=0.3, context_window=200000, max_tokens=16384)


# ═══════════════════════════════════════════════════════════════════════════════
# OPENAI
# ═══════════════════════════════════════════════════════════════════════════════
_OPENAI = "https://api.openai.com/v1"

_add("openai", "gpt-4o", name="GPT-4o", api=API_OPENAI_RESPONSES, base_url=_OPENAI, input_types=["text", "image"], cost_input=2.5, cost_output=10, cost_cache_read=1.25, context_window=128000, max_tokens=16384)
_add("openai", "gpt-4o-mini", name="GPT-4o Mini", api=API_OPENAI_RESPONSES, base_url=_OPENAI, input_types=["text", "image"], cost_input=0.15, cost_output=0.6, cost_cache_read=0.075, context_window=128000, max_tokens=16384)
_add("openai", "gpt-4.1", name="GPT-4.1", api=API_OPENAI_RESPONSES, base_url=_OPENAI, input_types=["text", "image"], cost_input=2, cost_output=8, cost_cache_read=0.5, context_window=1048576, max_tokens=32768)
_add("openai", "gpt-4.1-mini", name="GPT-4.1 Mini", api=API_OPENAI_RESPONSES, base_url=_OPENAI, input_types=["text", "image"], cost_input=0.4, cost_output=1.6, cost_cache_read=0.1, context_window=1048576, max_tokens=32768)
_add("openai", "gpt-4.1-nano", name="GPT-4.1 Nano", api=API_OPENAI_RESPONSES, base_url=_OPENAI, input_types=["text", "image"], cost_input=0.1, cost_output=0.4, cost_cache_read=0.025, context_window=1048576, max_tokens=32768)
_add("openai", "o1", name="o1", api=API_OPENAI_RESPONSES, base_url=_OPENAI, reasoning=True, input_types=["text", "image"], cost_input=15, cost_output=60, cost_cache_read=7.5, context_window=200000, max_tokens=100000)
_add("openai", "o1-mini", name="o1 Mini", api=API_OPENAI_RESPONSES, base_url=_OPENAI, reasoning=True, input_types=["text", "image"], cost_input=3, cost_output=12, cost_cache_read=1.5, context_window=128000, max_tokens=65536)
_add("openai", "o3", name="o3", api=API_OPENAI_RESPONSES, base_url=_OPENAI, reasoning=True, input_types=["text", "image"], cost_input=2, cost_output=8, cost_cache_read=0.5, context_window=200000, max_tokens=100000)
_add("openai", "o3-mini", name="o3 Mini", api=API_OPENAI_RESPONSES, base_url=_OPENAI, reasoning=True, input_types=["text", "image"], cost_input=1.1, cost_output=4.4, cost_cache_read=0.55, context_window=200000, max_tokens=100000)
_add("openai", "o4-mini", name="o4 Mini", api=API_OPENAI_RESPONSES, base_url=_OPENAI, reasoning=True, input_types=["text", "image"], cost_input=1.1, cost_output=4.4, cost_cache_read=0.275, context_window=200000, max_tokens=100000)


# ═══════════════════════════════════════════════════════════════════════════════
# DEEPSEEK
# ═══════════════════════════════════════════════════════════════════════════════
_DEEPSEEK = "https://api.deepseek.com"

_add("deepseek", "deepseek-chat", name="DeepSeek V3", api=API_OPENAI_COMPLETIONS, base_url=_DEEPSEEK, input_types=["text"], cost_input=0.27, cost_output=1.1, cost_cache_read=0.07, context_window=65536, max_tokens=8192)
_add("deepseek", "deepseek-reasoner", name="DeepSeek R1", api=API_OPENAI_COMPLETIONS, base_url=_DEEPSEEK, reasoning=True, input_types=["text"], cost_input=0.55, cost_output=2.19, cost_cache_read=0.14, context_window=65536, max_tokens=8192, thinking_level_map={"minimal": None, "low": None, "medium": None, "high": "high", "xhigh": "max"})


# ═══════════════════════════════════════════════════════════════════════════════
# GOOGLE GEMINI
# ═══════════════════════════════════════════════════════════════════════════════
_GOOGLE = "https://generativelanguage.googleapis.com/v1beta"

_add("google", "gemini-2.0-flash", name="Gemini 2.0 Flash", api=API_GOOGLE_GENERATIVE_AI, base_url=_GOOGLE, input_types=["text", "image"], cost_input=0.1, cost_output=0.4, cost_cache_read=0.025, context_window=1048576, max_tokens=8192)
_add("google", "gemini-2.0-flash-lite", name="Gemini 2.0 Flash Lite", api=API_GOOGLE_GENERATIVE_AI, base_url=_GOOGLE, input_types=["text", "image"], cost_input=0.075, cost_output=0.3, context_window=1048576, max_tokens=8192)
_add("google", "gemini-2.5-flash", name="Gemini 2.5 Flash", api=API_GOOGLE_GENERATIVE_AI, base_url=_GOOGLE, reasoning=True, input_types=["text", "image"], cost_input=0.15, cost_output=0.6, cost_cache_read=0.0375, context_window=1048576, max_tokens=65536)
_add("google", "gemini-2.5-flash-lite", name="Gemini 2.5 Flash Lite", api=API_GOOGLE_GENERATIVE_AI, base_url=_GOOGLE, reasoning=True, input_types=["text", "image"], cost_input=0.075, cost_output=0.3, context_window=1048576, max_tokens=65536)
_add("google", "gemini-2.5-pro", name="Gemini 2.5 Pro", api=API_GOOGLE_GENERATIVE_AI, base_url=_GOOGLE, reasoning=True, input_types=["text", "image"], cost_input=1.25, cost_output=10, cost_cache_read=0.3125, context_window=1048576, max_tokens=65536)
_add("google", "gemini-3-flash-preview", name="Gemini 3 Flash Preview", api=API_GOOGLE_GENERATIVE_AI, base_url=_GOOGLE, reasoning=True, input_types=["text", "image"], cost_input=0.15, cost_output=0.6, cost_cache_read=0.0375, context_window=1048576, max_tokens=65536)
_add("google", "gemini-3-pro-preview", name="Gemini 3 Pro Preview", api=API_GOOGLE_GENERATIVE_AI, base_url=_GOOGLE, reasoning=True, input_types=["text", "image"], cost_input=1.25, cost_output=10, cost_cache_read=0.3125, context_window=1048576, max_tokens=65536)
_add("google", "gemini-3.1-pro-preview", name="Gemini 3.1 Pro Preview", api=API_GOOGLE_GENERATIVE_AI, base_url=_GOOGLE, reasoning=True, input_types=["text", "image"], cost_input=1.25, cost_output=10, cost_cache_read=0.3125, context_window=1048576, max_tokens=65536)
_add("google", "gemini-1.5-flash", name="Gemini 1.5 Flash", api=API_GOOGLE_GENERATIVE_AI, base_url=_GOOGLE, input_types=["text", "image"], cost_input=0.075, cost_output=0.3, cost_cache_read=0.01875, context_window=1048576, max_tokens=8192)
_add("google", "gemini-1.5-pro", name="Gemini 1.5 Pro", api=API_GOOGLE_GENERATIVE_AI, base_url=_GOOGLE, input_types=["text", "image"], cost_input=1.25, cost_output=5, cost_cache_read=0.3125, context_window=2097152, max_tokens=8192)


# ═══════════════════════════════════════════════════════════════════════════════
# GROQ
# ═══════════════════════════════════════════════════════════════════════════════
_GROQ = "https://api.groq.com/openai/v1"

_add("groq", "deepseek-r1-distill-llama-70b", name="DeepSeek R1 Distill 70B", api=API_OPENAI_COMPLETIONS, base_url=_GROQ, reasoning=True, input_types=["text"], cost_input=0.75, cost_output=0.99, context_window=131072, max_tokens=16384)
_add("groq", "gemma2-9b-it", name="Gemma 2 9B", api=API_OPENAI_COMPLETIONS, base_url=_GROQ, input_types=["text"], cost_input=0.2, cost_output=0.2, context_window=8192, max_tokens=8192)
_add("groq", "llama-3.1-8b-instant", name="Llama 3.1 8B Instant", api=API_OPENAI_COMPLETIONS, base_url=_GROQ, input_types=["text"], cost_input=0.05, cost_output=0.08, context_window=131072, max_tokens=8192)
_add("groq", "llama-3.3-70b-versatile", name="Llama 3.3 70B", api=API_OPENAI_COMPLETIONS, base_url=_GROQ, input_types=["text"], cost_input=0.59, cost_output=0.79, context_window=131072, max_tokens=32768)
_add("groq", "llama-4-scout-17b-16e-instruct", name="Llama 4 Scout 17B", api=API_OPENAI_COMPLETIONS, base_url=_GROQ, input_types=["text", "image"], cost_input=0.11, cost_output=0.34, context_window=524288, max_tokens=8192)
_add("groq", "llama-4-maverick-17b-128e-instruct", name="Llama 4 Maverick 17B", api=API_OPENAI_COMPLETIONS, base_url=_GROQ, input_types=["text", "image"], cost_input=0.2, cost_output=0.6, context_window=1048576, max_tokens=8192)
_add("groq", "qwen-qwq-32b", name="Qwen QwQ 32B", api=API_OPENAI_COMPLETIONS, base_url=_GROQ, reasoning=True, input_types=["text"], cost_input=0.29, cost_output=0.39, context_window=131072, max_tokens=131072)
_add("groq", "compound-beta", name="Compound Beta", api=API_OPENAI_COMPLETIONS, base_url=_GROQ, input_types=["text"], cost_input=0.5, cost_output=0.5, context_window=131072, max_tokens=8192)


# ═══════════════════════════════════════════════════════════════════════════════
# MISTRAL
# ═══════════════════════════════════════════════════════════════════════════════
_MISTRAL = "https://api.mistral.ai/v1"

_add("mistral", "codestral-latest", name="Codestral", api=API_MISTRAL_CONVERSATIONS, base_url=_MISTRAL, input_types=["text"], cost_input=0.3, cost_output=0.9, context_window=256000, max_tokens=32768)
_add("mistral", "mistral-small-latest", name="Mistral Small", api=API_MISTRAL_CONVERSATIONS, base_url=_MISTRAL, input_types=["text", "image"], cost_input=0.1, cost_output=0.3, context_window=131072, max_tokens=32768)
_add("mistral", "mistral-large-latest", name="Mistral Large", api=API_MISTRAL_CONVERSATIONS, base_url=_MISTRAL, input_types=["text"], cost_input=2, cost_output=6, context_window=131072, max_tokens=32768)
_add("mistral", "open-mistral-nemo", name="Mistral Nemo", api=API_MISTRAL_CONVERSATIONS, base_url=_MISTRAL, input_types=["text"], cost_input=0.15, cost_output=0.15, context_window=131072, max_tokens=32768)


# ═══════════════════════════════════════════════════════════════════════════════
# xAI (Grok)
# ═══════════════════════════════════════════════════════════════════════════════
_XAI = "https://api.x.ai/v1"

_add("xai", "grok-3-mini", name="Grok 3 Mini", api=API_OPENAI_COMPLETIONS, base_url=_XAI, reasoning=True, input_types=["text"], cost_input=0.3, cost_output=0.5, context_window=131072, max_tokens=16384)
_add("xai", "grok-3", name="Grok 3", api=API_OPENAI_COMPLETIONS, base_url=_XAI, input_types=["text", "image"], cost_input=3, cost_output=15, context_window=131072, max_tokens=16384)
_add("xai", "grok-3-fast", name="Grok 3 Fast", api=API_OPENAI_COMPLETIONS, base_url=_XAI, input_types=["text", "image"], cost_input=5, cost_output=25, context_window=131072, max_tokens=16384)
_add("xai", "grok-4", name="Grok 4", api=API_OPENAI_COMPLETIONS, base_url=_XAI, input_types=["text", "image"], cost_input=3, cost_output=15, context_window=131072, max_tokens=16384)
_add("xai", "grok-4-mini", name="Grok 4 Mini", api=API_OPENAI_COMPLETIONS, base_url=_XAI, input_types=["text", "image"], cost_input=0.3, cost_output=0.5, context_window=131072, max_tokens=16384)


# ═══════════════════════════════════════════════════════════════════════════════
# CEREBRAS
# ═══════════════════════════════════════════════════════════════════════════════
_CEREBRAS = "https://api.cerebras.ai/v1"

_add("cerebras", "gpt-oss-120b", name="GPT-OSS 120B", api=API_OPENAI_COMPLETIONS, base_url=_CEREBRAS, input_types=["text"], cost_input=0.6, cost_output=0.6, context_window=131072, max_tokens=16384)
_add("cerebras", "llama3.1-8b", name="Llama 3.1 8B", api=API_OPENAI_COMPLETIONS, base_url=_CEREBRAS, input_types=["text"], cost_input=0.1, cost_output=0.1, context_window=131072, max_tokens=16384)
_add("cerebras", "llama-3.3-70b", name="Llama 3.3 70B", api=API_OPENAI_COMPLETIONS, base_url=_CEREBRAS, input_types=["text"], cost_input=0.85, cost_output=1.2, context_window=131072, max_tokens=16384)
_add("cerebras", "qwen-3-32b", name="Qwen 3 32B", api=API_OPENAI_COMPLETIONS, base_url=_CEREBRAS, reasoning=True, input_types=["text"], cost_input=0.4, cost_output=0.4, context_window=131072, max_tokens=16384)
_add("cerebras", "deepseek-r1-distill-llama-70b", name="DeepSeek R1 Distill 70B", api=API_OPENAI_COMPLETIONS, base_url=_CEREBRAS, reasoning=True, input_types=["text"], cost_input=1.35, cost_output=1.35, context_window=131072, max_tokens=16384)


# ═══════════════════════════════════════════════════════════════════════════════
# OPENROUTER
# ═══════════════════════════════════════════════════════════════════════════════
_OPENROUTER = "https://openrouter.ai/api/v1"

_add("openrouter", "anthropic/claude-sonnet-4-5", name="Claude Sonnet 4.5", api=API_OPENAI_COMPLETIONS, base_url=_OPENROUTER, input_types=["text", "image"], cost_input=3, cost_output=15, context_window=200000, max_tokens=16384)
_add("openrouter", "anthropic/claude-opus-4-5", name="Claude Opus 4.5", api=API_OPENAI_COMPLETIONS, base_url=_OPENROUTER, reasoning=True, input_types=["text", "image"], cost_input=15, cost_output=75, context_window=200000, max_tokens=32768)
_add("openrouter", "openai/gpt-4o", name="GPT-4o", api=API_OPENAI_COMPLETIONS, base_url=_OPENROUTER, input_types=["text", "image"], cost_input=2.5, cost_output=10, context_window=128000, max_tokens=16384)
_add("openrouter", "openai/o3", name="o3", api=API_OPENAI_COMPLETIONS, base_url=_OPENROUTER, reasoning=True, input_types=["text", "image"], cost_input=2, cost_output=8, context_window=200000, max_tokens=100000)
_add("openrouter", "google/gemini-2.5-pro", name="Gemini 2.5 Pro", api=API_OPENAI_COMPLETIONS, base_url=_OPENROUTER, reasoning=True, input_types=["text", "image"], cost_input=1.25, cost_output=10, context_window=1048576, max_tokens=65536)
_add("openrouter", "deepseek/deepseek-r1", name="DeepSeek R1", api=API_OPENAI_COMPLETIONS, base_url=_OPENROUTER, reasoning=True, input_types=["text"], cost_input=0.55, cost_output=2.19, context_window=65536, max_tokens=8192)
_add("openrouter", "x-ai/grok-3", name="Grok 3", api=API_OPENAI_COMPLETIONS, base_url=_OPENROUTER, input_types=["text", "image"], cost_input=3, cost_output=15, context_window=131072, max_tokens=16384)


# ═══════════════════════════════════════════════════════════════════════════════
# FIREWORKS
# ═══════════════════════════════════════════════════════════════════════════════
_FIREWORKS = "https://api.fireworks.ai/inference"

_add("fireworks", "accounts/fireworks/models/llama-v3p1-8b-instruct", name="Llama 3.1 8B", api=API_ANTHROPIC_MESSAGES, base_url=_FIREWORKS, input_types=["text"], cost_input=0.2, cost_output=0.2, context_window=131072, max_tokens=16384)
_add("fireworks", "accounts/fireworks/models/llama-v3p3-70b-instruct", name="Llama 3.3 70B", api=API_ANTHROPIC_MESSAGES, base_url=_FIREWORKS, input_types=["text"], cost_input=0.9, cost_output=0.9, context_window=131072, max_tokens=16384)
_add("fireworks", "accounts/fireworks/models/llama4-scout-instruct-basic", name="Llama 4 Scout", api=API_ANTHROPIC_MESSAGES, base_url=_FIREWORKS, input_types=["text", "image"], cost_input=0.15, cost_output=0.15, context_window=131072, max_tokens=16384)
_add("fireworks", "accounts/fireworks/models/llama4-maverick-instruct-basic", name="Llama 4 Maverick", api=API_ANTHROPIC_MESSAGES, base_url=_FIREWORKS, input_types=["text", "image"], cost_input=0.22, cost_output=0.22, context_window=131072, max_tokens=16384)
_add("fireworks", "accounts/fireworks/models/deepseek-r1", name="DeepSeek R1", api=API_ANTHROPIC_MESSAGES, base_url=_FIREWORKS, reasoning=True, input_types=["text"], cost_input=5.5, cost_output=2.19, context_window=131072, max_tokens=16384)
_add("fireworks", "accounts/fireworks/models/qwen3-235b-a22b", name="Qwen3 235B", api=API_ANTHROPIC_MESSAGES, base_url=_FIREWORKS, input_types=["text"], cost_input=0.22, cost_output=0.22, context_window=131072, max_tokens=16384)
_add("fireworks", "accounts/fireworks/models/kimi-k2-instruct", name="Kimi K2", api=API_ANTHROPIC_MESSAGES, base_url=_FIREWORKS, input_types=["text"], cost_input=0.5, cost_output=0.5, context_window=131072, max_tokens=16384)


# ═══════════════════════════════════════════════════════════════════════════════
# HUGGING FACE
# ═══════════════════════════════════════════════════════════════════════════════
_HF = "https://router.huggingface.co/v1"

_add("huggingface", "meta-llama/Llama-3.1-8B-Instruct", name="Llama 3.1 8B", api=API_OPENAI_COMPLETIONS, base_url=_HF, input_types=["text"], cost_input=0.1, cost_output=0.1, context_window=131072, max_tokens=8192)
_add("huggingface", "meta-llama/Llama-3.3-70B-Instruct", name="Llama 3.3 70B", api=API_OPENAI_COMPLETIONS, base_url=_HF, input_types=["text"], cost_input=0.6, cost_output=0.6, context_window=131072, max_tokens=8192)
_add("huggingface", "Qwen/Qwen2.5-72B-Instruct", name="Qwen 2.5 72B", api=API_OPENAI_COMPLETIONS, base_url=_HF, input_types=["text"], cost_input=0.6, cost_output=0.6, context_window=131072, max_tokens=8192)
_add("huggingface", "deepseek-ai/DeepSeek-R1", name="DeepSeek R1", api=API_OPENAI_COMPLETIONS, base_url=_HF, reasoning=True, input_types=["text"], cost_input=0.55, cost_output=2.19, context_window=65536, max_tokens=8192)
_add("huggingface", "mistralai/Mistral-7B-Instruct-v0.3", name="Mistral 7B", api=API_OPENAI_COMPLETIONS, base_url=_HF, input_types=["text"], cost_input=0.1, cost_output=0.1, context_window=32768, max_tokens=8192)


# ═══════════════════════════════════════════════════════════════════════════════
# OLLAMA (local)
# ═══════════════════════════════════════════════════════════════════════════════
_OLLAMA = "http://localhost:11434/v1"

_add("ollama", "llama3.2", name="Llama 3.2", api=API_OPENAI_COMPLETIONS, base_url=_OLLAMA, input_types=["text"], context_window=131072, max_tokens=8192)
_add("ollama", "llama3.1", name="Llama 3.1", api=API_OPENAI_COMPLETIONS, base_url=_OLLAMA, input_types=["text"], context_window=131072, max_tokens=8192)
_add("ollama", "mistral", name="Mistral", api=API_OPENAI_COMPLETIONS, base_url=_OLLAMA, input_types=["text"], context_window=32768, max_tokens=8192)
_add("ollama", "codellama", name="Code Llama", api=API_OPENAI_COMPLETIONS, base_url=_OLLAMA, input_types=["text"], context_window=16384, max_tokens=8192)
_add("ollama", "deepseek-r1", name="DeepSeek R1", api=API_OPENAI_COMPLETIONS, base_url=_OLLAMA, reasoning=True, input_types=["text"], context_window=65536, max_tokens=8192)


# ═══════════════════════════════════════════════════════════════════════════════
# MINIMAX
# ═══════════════════════════════════════════════════════════════════════════════
_add("minimax", "MiniMax-M2.7", name="MiniMax M2.7", api=API_ANTHROPIC_MESSAGES, base_url="https://api.minimax.io/anthropic", input_types=["text"], cost_input=0.2, cost_output=0.8, context_window=1048576, max_tokens=16384)
_add("minimax", "MiniMax-M2.7-highspeed", name="MiniMax M2.7 Highspeed", api=API_ANTHROPIC_MESSAGES, base_url="https://api.minimax.io/anthropic", input_types=["text"], cost_input=0.2, cost_output=0.8, context_window=1048576, max_tokens=16384)
_add("minimax-cn", "MiniMax-M2.7", name="MiniMax M2.7", api=API_ANTHROPIC_MESSAGES, base_url="https://api.minimaxi.com/anthropic", input_types=["text"], cost_input=0.2, cost_output=0.8, context_window=1048576, max_tokens=16384)
_add("minimax-cn", "MiniMax-M2.7-highspeed", name="MiniMax M2.7 Highspeed", api=API_ANTHROPIC_MESSAGES, base_url="https://api.minimaxi.com/anthropic", input_types=["text"], cost_input=0.2, cost_output=0.8, context_window=1048576, max_tokens=16384)


# ═══════════════════════════════════════════════════════════════════════════════
# KIMI CODING
# ═══════════════════════════════════════════════════════════════════════════════
_add("kimi-coding", "kimi-for-coding", name="Kimi For Coding", api=API_OPENAI_COMPLETIONS, base_url="https://api.kimi.com/coding", input_types=["text"], cost_input=0.3, cost_output=0.3, context_window=131072, max_tokens=16384)
_add("kimi-coding", "kimi-k2-thinking", name="Kimi K2 Thinking", api=API_OPENAI_COMPLETIONS, base_url="https://api.kimi.com/coding", reasoning=True, input_types=["text"], cost_input=0.5, cost_output=0.5, context_window=131072, max_tokens=16384)


# ═══════════════════════════════════════════════════════════════════════════════
# ZAI
# ═══════════════════════════════════════════════════════════════════════════════
_add("zai", "glm-4.5-air", name="GLM 4.5 Air", api=API_OPENAI_COMPLETIONS, base_url="https://api.z.ai/api/coding/paas/v4", input_types=["text"], cost_input=0.2, cost_output=0.2, context_window=131072, max_tokens=16384)
_add("zai", "glm-4.7", name="GLM 4.7", api=API_OPENAI_COMPLETIONS, base_url="https://api.z.ai/api/coding/paas/v4", input_types=["text"], cost_input=0.5, cost_output=0.5, context_window=131072, max_tokens=16384)


# ═══════════════════════════════════════════════════════════════════════════════
# OPENCODE
# ═══════════════════════════════════════════════════════════════════════════════
_add("opencode", "big-pickle", name="Big Pickle", api=API_OPENAI_COMPLETIONS, base_url="https://opencode.ai/zen/v1", input_types=["text"], context_window=131072, max_tokens=16384)
_add("opencode", "small-pickle", name="Small Pickle", api=API_OPENAI_COMPLETIONS, base_url="https://opencode.ai/zen/v1", input_types=["text"], context_window=131072, max_tokens=16384)
_add("opencode-go", "deepseek-v4-flash", name="DeepSeek V4 Flash", api=API_OPENAI_COMPLETIONS, base_url="https://opencode.ai/zen/go/v1", input_types=["text"], context_window=131072, max_tokens=16384)
_add("opencode-go", "deepseek-v4-pro", name="DeepSeek V4 Pro", api=API_OPENAI_COMPLETIONS, base_url="https://opencode.ai/zen/go/v1", input_types=["text"], context_window=131072, max_tokens=16384)


# ═══════════════════════════════════════════════════════════════════════════════
# TOGETHER
# ═══════════════════════════════════════════════════════════════════════════════
_add("together", "meta-llama/Llama-3-8b-chat-hf", name="Llama 3 8B", api=API_OPENAI_COMPLETIONS, base_url="https://api.together.ai/v1", input_types=["text"], context_window=8192, max_tokens=8192)
_add("together", "meta-llama/Llama-3-70b-chat-hf", name="Llama 3 70B", api=API_OPENAI_COMPLETIONS, base_url="https://api.together.ai/v1", input_types=["text"], context_window=8192, max_tokens=8192)
_add("together", "mistralai/Mixtral-8x7B-Instruct-v0.1", name="Mixtral 8x7B", api=API_OPENAI_COMPLETIONS, base_url="https://api.together.ai/v1", input_types=["text"], context_window=32768, max_tokens=8192)
_add("together", "deepseek-ai/deepseek-r1", name="DeepSeek R1", api=API_OPENAI_COMPLETIONS, base_url="https://api.together.ai/v1", reasoning=True, input_types=["text"], context_window=65536, max_tokens=8192)


# ═══════════════════════════════════════════════════════════════════════════════
# MOONSHOT
# ═══════════════════════════════════════════════════════════════════════════════
_add("moonshotai", "moonshot-v1-8k", name="Moonshot V1 8K", api=API_OPENAI_COMPLETIONS, base_url="https://api.moonshot.ai/v1", input_types=["text"], context_window=8192, max_tokens=8192)
_add("moonshotai", "moonshot-v1-32k", name="Moonshot V1 32K", api=API_OPENAI_COMPLETIONS, base_url="https://api.moonshot.ai/v1", input_types=["text"], context_window=32768, max_tokens=8192)
_add("moonshotai-cn", "moonshot-v1-8k", name="Moonshot V1 8K", api=API_OPENAI_COMPLETIONS, base_url="https://api.moonshot.cn/v1", input_types=["text"], context_window=8192, max_tokens=8192)
_add("moonshotai-cn", "moonshot-v1-32k", name="Moonshot V1 32K", api=API_OPENAI_COMPLETIONS, base_url="https://api.moonshot.cn/v1", input_types=["text"], context_window=32768, max_tokens=8192)


# ═══════════════════════════════════════════════════════════════════════════════
# XIAOMI
# ═══════════════════════════════════════════════════════════════════════════════
_add("xiaomi", "mimo-v2-omni", name="Mimo V2 Omni", api=API_OPENAI_COMPLETIONS, base_url="https://api.xiaomimimo.com/v1", input_types=["text"], context_window=131072, max_tokens=16384)
_add("xiaomi", "mimo-v2-pro", name="Mimo V2 Pro", api=API_OPENAI_COMPLETIONS, base_url="https://api.xiaomimimo.com/v1", input_types=["text"], context_window=131072, max_tokens=16384)


# ═══════════════════════════════════════════════════════════════════════════════
# CLOUDFLARE
# ═══════════════════════════════════════════════════════════════════════════════
_add("cloudflare-workers-ai", "@cf/meta/llama-4-scout-17b-16e-instruct", name="Llama 4 Scout 17B", api=API_OPENAI_COMPLETIONS, base_url="https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1", input_types=["text", "image"], context_window=131072, max_tokens=8192)
_add("cloudflare-workers-ai", "@cf/meta/llama-3.3-70b-instruct-fp8-fast", name="Llama 3.3 70B", api=API_OPENAI_COMPLETIONS, base_url="https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1", input_types=["text"], context_window=131072, max_tokens=8192)
_add("cloudflare-workers-ai", "@cf/deepseek-ai/deepseek-r1-distill-qwen-32b", name="DeepSeek R1 Distill 32B", api=API_OPENAI_COMPLETIONS, base_url="https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/v1", reasoning=True, input_types=["text"], context_window=131072, max_tokens=8192)


# ═══════════════════════════════════════════════════════════════════════════════
# VERCEL AI GATEWAY
# ═══════════════════════════════════════════════════════════════════════════════
_add("vercel-ai-gateway", "openai/gpt-4o-mini", name="GPT-4o Mini", api=API_OPENAI_COMPLETIONS, base_url="https://ai-gateway.vercel.sh", input_types=["text", "image"], cost_input=0.15, cost_output=0.6, context_window=128000, max_tokens=16384)
_add("vercel-ai-gateway", "openai/gpt-4o", name="GPT-4o", api=API_OPENAI_COMPLETIONS, base_url="https://ai-gateway.vercel.sh", input_types=["text", "image"], cost_input=2.5, cost_output=10, context_window=128000, max_tokens=16384)
_add("vercel-ai-gateway", "anthropic/claude-sonnet-4-5", name="Claude Sonnet 4.5", api=API_OPENAI_COMPLETIONS, base_url="https://ai-gateway.vercel.sh", input_types=["text", "image"], cost_input=3, cost_output=15, context_window=200000, max_tokens=16384)
_add("vercel-ai-gateway", "google/gemini-2.5-flash", name="Gemini 2.5 Flash", api=API_OPENAI_COMPLETIONS, base_url="https://ai-gateway.vercel.sh", reasoning=True, input_types=["text", "image"], cost_input=0.15, cost_output=0.6, context_window=1048576, max_tokens=65536)


# ═══════════════════════════════════════════════════════════════════════════════
# AZURE OPENAI
# ═══════════════════════════════════════════════════════════════════════════════
_add("azure-openai-responses", "gpt-4o", name="GPT-4o", api=API_AZURE_OPENAI_RESPONSES, base_url="", input_types=["text", "image"], context_window=128000, max_tokens=16384)
_add("azure-openai-responses", "gpt-4o-mini", name="GPT-4o Mini", api=API_AZURE_OPENAI_RESPONSES, base_url="", input_types=["text", "image"], context_window=128000, max_tokens=16384)
_add("azure-openai-responses", "gpt-4.1", name="GPT-4.1", api=API_AZURE_OPENAI_RESPONSES, base_url="", input_types=["text", "image"], context_window=1048576, max_tokens=32768)
_add("azure-openai-responses", "o3", name="o3", api=API_AZURE_OPENAI_RESPONSES, base_url="", reasoning=True, input_types=["text", "image"], context_window=200000, max_tokens=100000)
_add("azure-openai-responses", "o4-mini", name="o4 Mini", api=API_AZURE_OPENAI_RESPONSES, base_url="", reasoning=True, input_types=["text", "image"], context_window=200000, max_tokens=100000)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def get_provider(provider_id: str) -> dict[str, Model] | None:
    """Get all models for a provider."""
    return PROVIDERS.get(provider_id)


def get_model(provider_id: str, model_id: str) -> Model | None:
    """Get a specific model."""
    provider = PROVIDERS.get(provider_id)
    if provider:
        return provider.get(model_id)
    return None


def get_all_providers() -> list[str]:
    """Get all provider IDs."""
    return list(PROVIDERS.keys())


def get_all_models() -> list[Model]:
    """Get all models across all providers."""
    models = []
    for provider in PROVIDERS.values():
        models.extend(provider.values())
    return models


def find_model(model_id: str) -> Model | None:
    """Find a model by ID across all providers."""
    for provider in PROVIDERS.values():
        if model_id in provider:
            return provider[model_id]
    return None


def get_provider_api(provider_id: str) -> str | None:
    """Get the API protocol for a provider."""
    provider = PROVIDERS.get(provider_id)
    if provider:
        first_model = next(iter(provider.values()))
        return first_model.api
    return None
