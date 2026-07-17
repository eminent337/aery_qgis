"""Regression guard for Step 5: max_tokens must be wired from the provider
registry (providers.Model), not hardcoded at 8192 for every model.

Before the fix, Model.__init__ had no max_tokens attribute and no _add call
set it, so Agent._get_model_max_tokens() always fell through to the 8192
default — large-output models (e.g. Gemini Pro) were silently capped.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from aery_plugin.providers import get_model, _default_max_tokens


def test_model_exposes_max_tokens_attribute():
    m = get_model("google-antigravity", "gemini-3-pro-high")
    assert hasattr(m, "max_tokens")
    assert isinstance(m.max_tokens, int)
    assert m.max_tokens > 0


def test_gemini_pro_gets_large_output_budget():
    # Gemini Pro family supports 64k+ outputs; must not be capped at 8192.
    assert get_model("google-antigravity", "gemini-3-pro-high").max_tokens == 65536
    assert get_model("google-antigravity", "gemini-3.1-pro-low").max_tokens == 65536


def test_gemini_flash_uses_default():
    assert get_model("google-antigravity", "gemini-3-flash").max_tokens == 8192


def test_active_stepfun_model_gets_reasonable_budget():
    # The user's configured model (StepFun 3.7 Flash via kilo) must resolve
    # to a positive int, not crash the lookup.
    m = get_model("kilo", "stepfun/step-3.7-flash")
    assert m is not None
    assert m.max_tokens == 8192


def test_explicit_max_tokens_override_wins():
    from aery_plugin.providers import Model

    custom = Model(
        id="custom/x", name="X", api="openai", provider="kilo",
        base_url="https://example.com/v1", max_tokens=16384,
    )
    assert custom.max_tokens == 16384


def test_default_heuristic_family_budgets():
    assert _default_max_tokens("gemini-3-pro") == 65536
    assert _default_max_tokens("gpt-5.4") == 32768
    assert _default_max_tokens("claude-sonnet-4-6") == 8192
    assert _default_max_tokens("some-unknown-model") == 8192
