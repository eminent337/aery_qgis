"""Classifies and formats tool execution errors with actionable hints."""

import re
import json
import traceback
from typing import Any


_PATTERNS: list[tuple[str, "re.Pattern[str]", str]] = [
    (
        "name_error",
        re.compile(r"^NameError:\s+name\s+'([^']+)'\s+is\s+not\s+defined"),
        "Variable {0} is not defined. If you created it earlier in this session, "
        "it may have been lost between turns — re-establish it. If it's a layer or "
        "algorithm parameter, use resolve_layer() / processing.run() helpers.",
    ),
    (
        "type_error_qgsfield",
        re.compile(r"^TypeError:\s+QgsField\(\):\s+arguments\s+did\s+not\s+match"),
        "QgsField does not accept raw integers or QVariant members for the type "
        "argument in PyQt6. Use QMetaType.Type.QString / .Int / .Double (e.g. "
        "QgsField('name', QMetaType.Type.QString)).",
    ),
    (
        "type_error",
        re.compile(
            r"^TypeError:\s+(\w+)\(\)\s+got\s+an\s+unexpected\s+keyword\s+argument\s+'([^']+)'"
        ),
        "Function {0} does not accept keyword argument '{1}'. Check the function "
        "signature — common offenders: set_layer_style_simple expects (layer_id, "
        "style_type, color_or_ramp, column), not (layer_id, color).",
    ),
    (
        "attribute_error",
        re.compile(r"^AttributeError:\s+'([^']+)'\s+object\s+has\s+no\s+attribute\s+'([^']+)'"),
        "Object {0} has no attribute '{1}'. If this is a string holding a layer "
        "name/ID, resolve it first with resolve_layer(). If it's a real QGIS "
        "object, verify the API name against the QGIS version.",
    ),
    (
        "key_error",
        re.compile(r"^KeyError:\s+'([^']+)'"),
        "Key '{0}' is missing. For feature attributes, the field may not exist on "
        "the layer — use layer.fields().names() to list available fields. For "
        "dict keys, check the processing algorithm parameters.",
    ),
    (
        "index_error",
        re.compile(r"^IndexError:\s+list\s+index\s+out\s+of\s+range"),
        "List index out of range. Check that the list is non-empty before indexing "
        "(e.g. `if layers: layer = layers[0]`) or use `next(iter, None)`.",
    ),
    (
        "not_subscriptable",
        re.compile(r"^TypeError:\s+'([^']+)'\s+object\s+is\s+not\s+subscriptable"),
        "{0} is not subscriptable. layer.getFeatures() returns a "
        "QgsFeatureIterator — use `next(layer.getFeatures(), None)` instead of "
        "getFeatures()[0].",
    ),
    (
        "import_error",
        re.compile(r"^ImportError:\s+(?:cannot\s+import\s+name\s+)'?([^'\s]+)'?"),
        "Cannot import {0}. This module/class may not be available in QGIS's "
        "Python environment or may need to be added to the preloaded globals.",
    ),
    (
        "sandbox_violation",
        re.compile(r"^.*Sandbox violation"),
        "Sandbox blocked this call. Check the TOOL RULES for forbidden builtins "
        "(type, open, __import__). Use the dedicated tool (run_shell, "
        "run_python_script) for unrestricted Python.",
    ),
    (
        "algorithm_failed",
        re.compile(r"^RuntimeError:\s+Algorithm\s+'([^']+)'\s+failed"),
        "Algorithm {0} failed. Common causes: (1) wrong parameter types — pass "
        "numbers as 150.0 not '150'; (2) 'OUTPUT' must be 'TEMPORARY_OUTPUT' for "
        "memory layers, never 'memory:'; (3) layer reference needs to be "
        "resolved from name/ID.",
    ),
]


RETRYABLE_CATEGORIES = frozenset({
    "algorithm_failed",
})


def classify(error: BaseException) -> dict[str, Any]:
    """Classify an exception and return a structured envelope."""
    raw = str(error)
    type_name = type(error).__name__
    first_line = raw.split("\n", 1)[0].strip()
    if not first_line.startswith(type_name + ":"):
        first_line = f"{type_name}: {first_line}"

    for category, pattern, hint_template in _PATTERNS:
        m = pattern.match(first_line)
        if m:
            try:
                hint = hint_template.format(*m.groups())
            except (IndexError, KeyError):
                hint = hint_template
            return {
                "category": category,
                "message": first_line,
                "hint": hint,
                "retryable": category in RETRYABLE_CATEGORIES,
                "traceback": traceback.format_exc(),
            }

    return {
        "category": "unknown",
        "message": first_line or raw,
        "hint": None,
        "retryable": False,
        "traceback": traceback.format_exc(),
    }


def format_for_agent(error: BaseException) -> str:
    """Return a human-readable string for the agent with hint appended."""
    env = classify(error)
    parts = [env["message"]]
    if env["hint"]:
        parts.append(f"\nHint: {env['hint']}")
    else:
        parts.append("\nHint: (no hint available for this error category)")
    parts.append(
        f"\nCategory: {env['category']} | Retryable: {'yes' if env['retryable'] else 'no'}"
    )
    return "\n".join(parts)


def format_structured(error: BaseException) -> str:
    """Return a JSON string with the structured envelope (no traceback)."""
    env = classify(error)
    out = {
        "category": env["category"],
        "message": env["message"],
        "hint": env["hint"],
        "retryable": env["retryable"],
    }
    return json.dumps(out, indent=2)


def classify_from_string(error_str: str) -> dict[str, Any]:
    """Classify a pre-formatted error string (no exception object available)."""
    raw = error_str or ""
    first_line = raw.split("\n", 1)[0].strip()

    for category, pattern, hint_template in _PATTERNS:
        m = pattern.match(first_line)
        if m:
            try:
                hint = hint_template.format(*m.groups())
            except (IndexError, KeyError):
                hint = hint_template
            return {
                "category": category,
                "message": first_line,
                "hint": hint,
                "retryable": category in RETRYABLE_CATEGORIES,
                "traceback": "",
            }

    return {
        "category": "unknown",
        "message": first_line or raw,
        "hint": None,
        "retryable": False,
        "traceback": "",
    }


def wrap_tool_error(tool_name: str, error_str: str) -> str:
    """Wrap a tool error string with hint and category for the agent.

    Returns the original string with hint and category appended on new lines.
    If the error is unknown, returns the original string unchanged.
    """
    env = classify_from_string(error_str)
    if env["category"] == "unknown" and not env["hint"]:
        return error_str
    parts = [
        error_str,
        "",
        f"[Aery error classifier] Category: {env['category']} | Tool: {tool_name}",
    ]
    if env["hint"]:
        parts.append(f"Hint: {env['hint']}")
    if env["retryable"]:
        parts.append("(This error category is often transient — retry may succeed.)")
    return "\n".join(parts)
