"""
QGIS Integration Test Script for Processing Algorithm Discovery & Execution.

Run this script in QGIS Python Console to verify that:
1.  The discover_qgis_algorithms tool can find real algorithms
2.  The validate_algorithm_run tool validates parameters correctly
3.  Auto-generated tool definitions from generate_algorithm_tool_defs are valid
4.  A real algorithm can be discovered and described

Usage:
    In QGIS Python Console:
        exec(open('/path/to/test_qgis_integration.py').read())
"""

import sys
import json
import os

# Add plugin directory to path
PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)

passed = 0
failed = 0


def _test_helper(name, condition, detail=""):
    global passed, failed
    if condition:
        passed += 1
        print(f"  ✓ {name}")
    else:
        failed += 1
        print(f"  ✗ {name} -- FAILED{': ' + detail if detail else ''}")


def run():
    global passed, failed
    passed = 0
    failed = 0

    print("=" * 60)
    print("QGIS PROCESSING INTEGRATION TESTS")
    print("=" * 60)

    # ── 1. Module Import ──
    print("\n--- Module Imports ---")
    try:
        from aery_plugin.processing_discovery import (
            PROCESSING_DISCOVERY_TOOLS,
            discover_qgis_algorithms,
            validate_algorithm_run,
            generate_algorithm_tool_defs,
            _serialize_algorithm,
        )
        _test_helper("processing_discovery module imports", True)
    except ImportError as e:
        _test_helper("processing_discovery module imports", False, str(e))
        print("\nCannot continue without module imports.")
        print(f"\nResults: {passed} passed, {failed} failed")
        return

    # ── 2. QGIS Environment ──
    print("\n--- QGIS Environment ---")
    try:
        from qgis.core import QgsApplication
        _test_helper("QgsApplication importable", True)
        registry = QgsApplication.processingRegistry()
        _test_helper("processingRegistry available", registry is not None)
        if registry:
            alg_count = len(list(registry.algorithms()))
            _test_helper("processingRegistry has algorithms", alg_count > 0, str(alg_count))
            print(f"      Total registered algorithms: {alg_count}")
    except Exception as e:
        _test_helper("QGIS environment check", False, str(e))

    # ── 3. Discover Algorithms ──
    print("\n--- discover_qgis_algorithms ---")

    # Test keyword search
    result = discover_qgis_algorithms(keyword="buffer")
    _test_helper("keyword='buffer' returns dict", isinstance(result, dict))
    _test_helper("keyword='buffer' has algorithms key", "algorithms" in result)
    _test_helper("keyword='buffer' finds matches", result.get("count", 0) > 0, str(result.get("count", 0)))
    if result.get("count", 0) > 0:
        first = result["algorithms"][0]
        _test_helper("result has algorithm id", "id" in first)
        _test_helper("result has algorithm name", "name" in first)
        _test_helper("result has algorithm group", "group" in first)
        print(f"      First match: {first.get('id')} - {first.get('name')}")

    # Test specific algorithm lookup
    alg_id = "native:buffer"
    specific = discover_qgis_algorithms(algorithm_id=alg_id, include_parameters=True)
    _test_helper(f"algorithm_id='{alg_id}' returns dict", isinstance(specific, dict))
    _test_helper(f"'{alg_id}' found", specific.get("count", 0) == 1, str(specific))
    if specific.get("count", 0) > 0:
        alg = specific["algorithms"][0]
        _test_helper("has parameter definitions", "input_parameters" in alg or "output_parameters" in alg)
        print(f"      Algorithm: {alg.get('name')}")
        inputs = alg.get("input_parameters", [])
        outputs = alg.get("output_parameters", [])
        print(f"      Input params: {len(inputs)}, Output params: {len(outputs)}")

    # Test unknown algorithm
    unknown = discover_qgis_algorithms(algorithm_id="nonexistent:fake")
    _test_helper("unknown algorithm returns error", "error" in unknown)

    # Test no args
    no_args = discover_qgis_algorithms()
    _test_helper("no args returns error", "error" in no_args)

    # ── 4. validate_algorithm_run ──
    print("\n--- validate_algorithm_run ---")

    val = validate_algorithm_run(algorithm_id=alg_id)
    _test_helper(f"validate '{alg_id}' returns dict", isinstance(val, dict))
    _test_helper(f"'{alg_id}' is valid", val.get("valid", False) is True)
    _test_helper("result has algorithm_name", "algorithm_name" in val)
    _test_helper("result has algorithm_group", "algorithm_group" in val)

    val_unknown = validate_algorithm_run(algorithm_id="nonexistent:fake")
    _test_helper("unknown algorithm is invalid", val_unknown.get("valid", True) is False)
    _test_helper("unknown returns error", "error" in val_unknown)

    val_no_id = validate_algorithm_run()
    _test_helper("no algorithm_id is invalid", val_no_id.get("valid", True) is False)

    # ── 5. generate_algorithm_tool_defs ──
    print("\n--- generate_algorithm_tool_defs ---")

    tool_defs = generate_algorithm_tool_defs()
    _test_helper("returns list", isinstance(tool_defs, list))
    _test_helper("has tools", len(tool_defs) > 0, str(len(tool_defs)))
    print(f"      Generated {len(tool_defs)} tool definitions")

    if len(tool_defs) > 0:
        # Check first few tool defs
        for i, td in enumerate(tool_defs[:3]):
            _test_helper(f"tool {i+1} has name", "name" in td and td["name"], td.get("name", ""))
            _test_helper(f"tool {i+1} has description", "description" in td and td["description"], "")
            _test_helper(f"tool {i+1} has algorithm_id", "algorithm_id" in td and td["algorithm_id"])
            _test_helper(f"tool {i+1} has parameters", "parameters" in td)
            if "parameters" in td:
                _test_helper(f"tool {i+1} params is object", td["parameters"].get("type") == "object")
                _test_helper(f"tool {i+1} params has properties", "properties" in td["parameters"])
            print(f"      Tool {i+1}: {td.get('name')} -> {td.get('algorithm_id')}")

    # ── 6. PROCESSING_DISCOVERY_TOOLS ──
    print("\n--- PROCESSING_DISCOVERY_TOOLS ---")

    _test_helper("TOOLS is a list", isinstance(PROCESSING_DISCOVERY_TOOLS, list))
    _test_helper("TOOLS has items", len(PROCESSING_DISCOVERY_TOOLS) > 0, str(len(PROCESSING_DISCOVERY_TOOLS)))

    for tool in PROCESSING_DISCOVERY_TOOLS:
        _test_helper(f"tool '{tool.get('name')}' has name", "name" in tool and tool["name"])
        _test_helper(f"tool '{tool.get('name')}' has execute", "execute" in tool and callable(tool["execute"]))
        _test_helper(f"tool '{tool.get('name')}' has description", "description" in tool and len(tool["description"]) > 50)
        _test_helper(f"tool '{tool.get('name')}' has parameters", "parameters" in tool)
        if "parameters" in tool:
            _test_helper(f"tool '{tool.get('name')}' params has type=object", tool["parameters"].get("type") == "object")
            _test_helper(f"tool '{tool.get('name')}' params has properties", len(tool["parameters"].get("properties", {})) > 0)

    # Check validate_algorithm_run is in the list
    tool_names = [t["name"] for t in PROCESSING_DISCOVERY_TOOLS]
    _test_helper("validate_algorithm_run is registered", "validate_algorithm_run" in tool_names)
    _test_helper("discover_qgis_algorithms is registered", "discover_qgis_algorithms" in tool_names)

    # ── Results ──
    print("" + "=" * 60)
    print(f"RESULTS: {passed} passed, {failed} failed")
    print("=" * 60)
    return failed == 0


if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
