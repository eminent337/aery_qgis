#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
QGIS Processing script to smoke-test all Aery plugin modules.
Run with: qgis_process run /path/to/smoke_test.py
"""
from qgis.core import (QgsProcessingAlgorithm, QgsProcessingParameterFile,
import sys, importlib, traceback as tb, os

class AerySmokeTest(QgsProcessingAlgorithm):
    def name(self):
        return "aery_smoke_test"
    def displayName(self):
        return "Aery Smoke Test"
    def group(self):
        return "Aery"
    def groupId(self):
        return "aery"
    @staticmethod
    def createInstance():
        return AerySmokeTest()
    def processAlgorithm(self, parameters, context, feedback):
        pl = os.path.join(os.path.expanduser("~"), ".local/share/QGIS/QGIS4/profiles/default/python/plugins")
        if pl not in sys.path:
            sys.path.insert(0, pl)

        passed = []
        failed = []

        def chk(name, fn):
            try:
                fn()
                passed.append(name)
            except Exception as e:
                failed.append((name, str(e)))

        chk("chat_panel", lambda: importlib.import_module("aery_plugin.chat_panel"))
        def check_exec():
            from aery_plugin.qgis_executor import _build_globals
            g = _build_globals()
            miss = [k for k in [
                "QgsPointCloudLayer","QgsPrintLayout","QgsLayoutExporter",
                "QgsLayoutItemLegend","QgsLayoutItemScaleBar","QgsLayoutItemNorthArrow",
                "QgsLayoutItemLabel","QgsLayoutItemPage","QgsLayoutMeasurement",
                "QgsLayoutUnit","QgsPageLayout","QgsSymbolLayer"
            ] if k not in g]
            if miss:
                raise AssertionError(f"Missing from globals: {miss}")
        chk("qgis_executor_globals", check_exec)
        chk("oauth_helper", lambda: importlib.import_module("aery_plugin.oauth_helper"))
        chk("tool_registry", lambda: importlib.import_module("aery_plugin.tool_registry"))
        chk("provider_settings", lambda: importlib.import_module("aery_plugin.provider_settings"))
        chk("graph_engine", lambda: importlib.import_module("aery_plugin.graph_engine"))

        lines = [f"{len(passed)}/{len(passed)+len(failed)} checks passed"]
        if failed:
            for n, e in failed:
                lines.append(f"  FAILED: {n}: {e}")
        report = "\n".join(lines)
        if failed:
            raise RuntimeError(report + "\n(see above)")
        print(report)
        print("ALL MODULES LOAD CLEANLY")
        return {"PASSED": str(passed), "FAILED": str(failed) if failed else ""}
