"""
Startup profiling script for QGIS Processing Discovery.

Measures how long it takes to:
1. Import the processing_discovery module
2. Generate algorithm tool definitions (calls QGIS Processing registry)
3. Estimate total tool registration overhead for 800+ algorithms

Usage:
    In QGIS Python Console:
        exec(open('/path/to/tests/test_profile_startup.py').read())
"""

import time
import sys
import os

# Add plugin directory to path
PLUGIN_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PLUGIN_DIR not in sys.path:
    sys.path.insert(0, PLUGIN_DIR)


def profile_startup():
    """Profile each phase of the processing discovery startup."""
    print("=" * 60)
    print("STARTUP PROFILING: QGIS Processing Discovery")
    print("=" * 60)

    # ── 1. Module import ──
    t0 = time.time()
    from aery_plugin.processing_discovery import (
        PROCESSING_DISCOVERY_TOOLS,
        generate_algorithm_tool_defs,
    )
    t1 = time.time()
    import_time = t1 - t0
    print(f"\n1. Module import:              {import_time*1000:.1f}ms")
    print(f"   Tools registered:           {len(PROCESSING_DISCOVERY_TOOLS)}")

    # ── 2. Generate algorithm tool defs ──
    t0 = time.time()
    tool_defs = generate_algorithm_tool_defs()
    t1 = time.time()
    gen_time = t1 - t0
    print(f"\n2. Generate tool definitions:  {gen_time*1000:.1f}ms")
    print(f"   Algorithms found:           {len(tool_defs)}")

    if tool_defs:
        # Show first/last few
        print(f"   First algorithm:            {tool_defs[0].get('name', 'N/A')}")
        if len(tool_defs) > 1:
            print(f"   Last algorithm:             {tool_defs[-1].get('name', 'N/A')}")

        # ── 3. Measure single registration cost ──
        # Estimate: measure serializing one algorithm vs all
        sample = tool_defs[:min(10, len(tool_defs))]
        t0 = time.time()
        _ = [td["parameters"] for td in sample]
        t1 = time.time()
        avg_param_access = (t1 - t0) / len(sample) if sample else 0
        print(f"\n3. Per-tool stats:")
        print(f"   Avg param schema access:   {avg_param_access*1000:.3f}ms")
        estimated_register = avg_param_access * len(tool_defs) * 3  # ×3 for name/desc/params processing
        print(f"   Estimated registration:    {estimated_register*1000:.1f}ms (all {len(tool_defs)} tools)")

    # ── 4. Memory estimate ──
    import sys as _sys
    if tool_defs:
        sample_size = _sys.getsizeof(tool_defs[0])
        estimated_memory = sample_size * len(tool_defs)
        print(f"\n4. Memory estimate:")
        print(f"   Per tool def (approx):     {sample_size} bytes")
        print(f"   Total ({len(tool_defs)} tools):           {estimated_memory / 1024:.0f} KB")

    # ── Summary ──
    print("\n" + "=" * 60)
    print(f"TOTAL ESTIMATED STARTUP COST:")
    print(f"   Import:           {import_time*1000:.1f}ms")
    print(f"   Generate defs:    {gen_time*1000:.1f}ms")
    print(f"   Register tools:   {estimated_register*1000:.1f}ms (estimated)")
    print(f"   Total:            {(import_time + gen_time + estimated_register)*1000:.1f}ms")
    print(f"   Algorithms:       {len(tool_defs)}")
    print(f"   Tools:            {len(PROCESSING_DISCOVERY_TOOLS)} static + {len(tool_defs)} auto-gen")
    print("=" * 60)


if __name__ == "__main__":
    profile_startup()
