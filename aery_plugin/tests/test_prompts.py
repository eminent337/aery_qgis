"""Unit tests for aery_plugin.prompts system prompt generation."""

import os
import sys
import unittest

# Ensure the plugin package is importable when running pytest from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from aery_plugin.prompts import build_system_prompt


class TestPrompts(unittest.TestCase):
    def test_build_system_prompt_default_includes_key_sections(self):
        prompt = build_system_prompt()
        self.assertIsNotNone(prompt)
        self.assertIn("=== TOOL RULES ===", prompt)
        self.assertIn("=== TOOL RULES ===", prompt)
        self.assertIn("Multi-Step Workflows and Task Splitting", prompt)
        self.assertIn("create_scratch_layer", prompt)

    def test_build_system_prompt_conditional_raster(self):
        # Without keywords, defaults to including everything
        prompt_default = build_system_prompt()
        self.assertIn("=== RASTER ANALYSIS ===", prompt_default)

        # With specific non-raster task description, raster section is excluded
        prompt_vector = build_system_prompt("Perform a vector clip and buffer operation")
        self.assertNotIn("=== RASTER ANALYSIS ===", prompt_vector)
        self.assertIn("=== VECTOR DATA MANIPULATION ===", prompt_vector)

        # With raster keyword, raster section is included
        prompt_raster = build_system_prompt("Calculate NDVI using Landsat raster imagery")
        self.assertIn("=== RASTER ANALYSIS ===", prompt_raster)
        self.assertNotIn("=== VECTOR DATA MANIPULATION ===", prompt_raster)

    def test_build_system_prompt_includes_env_version(self):
        """Version pinning: prompt must surface the runtime QGIS/Qt versions
        so the LLM can adapt to API differences across QGIS 3.x and 4.x."""
        prompt = build_system_prompt()
        self.assertIn("QGIS", prompt)
        self.assertIn("PyQt6", prompt)
    def test_greeting_profile_is_compact(self):
        prompt = build_system_prompt("hi")
        self.assertLess(len(prompt), 1_000)
        self.assertIn("Reply briefly", prompt)
        self.assertNotIn("=== QGIS CORE TOOLS ===", prompt)
    def test_basemap_profile_is_action_first_and_compact(self):
        prompt = build_system_prompt("load OSM basemap")
        self.assertLess(len(prompt), 1_500)
        self.assertIn("QGIS assistant inside the user's project", prompt)
        self.assertNotIn("PLAN:", prompt)
        self.assertNotIn("=== RASTER ANALYSIS ===", prompt)
    def test_complex_task_keeps_specialist_guidance(self):
        prompt = build_system_prompt("Calculate NDVI using Landsat raster imagery")
        self.assertIn("=== RASTER ANALYSIS ===", prompt)
        self.assertIn("=== TOOL RULES ===", prompt)

    def test_prompts_instruct_context_reuse(self):
        self.assertIn("Reuse a prior project-context result", build_system_prompt("zoom to roads"))
        self.assertIn("Reuse a prior project-context result", build_system_prompt("buffer the roads layer"))
