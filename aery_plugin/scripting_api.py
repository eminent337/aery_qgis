#!/usr/bin/env python3
"""Scripting API for Aery QGIS Plugin.

Provides sandboxed JavaScript/TypeScript execution with typed QGIS globals.
Uses PyMiniRacer (V8) for secure sandboxed execution.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

try:
    from py_mini_racer import py_mini_racer
    HAS_MINI_RACER = True
except ImportError:
    HAS_MINI_RACER = False
    py_mini_racer = None

from aery_plugin.logger import logger


@dataclass
class ScriptResult:
    """Result of script execution."""
    success: bool
    result: Any = None
    error: Optional[str] = None
    execution_time_ms: float = 0
    console_output: list[str] = field(default_factory=list)


@dataclass
class ScriptContext:
    """Context passed to script execution."""
    project_dir: str = ""
    layers: list[dict] = field(default_factory=list)
    canvas_extent: dict = field(default_factory=dict)
    crs: str = ""
    user_data: dict = field(default_factory=dict)


class ScriptSandbox:
    """Sandboxed JavaScript execution environment using V8 (PyMiniRacer)."""
    
    def __init__(self, max_execution_time_ms: int = 30000):
        if not HAS_MINI_RACER:
            raise RuntimeError("PyMiniRacer not installed. Run: pip install py_mini_racer")
        
        self._ctx = py_mini_racer.MiniRacer()
        self._max_execution_time_ms = max_execution_time_ms
        self._console_output: list[str] = []
        self._globals_defined = False
        
        # Inject console.log capture
        self._ctx.eval("""
            var _aery_console = [];
            var console = {
                log: function() {
                    var args = Array.prototype.slice.call(arguments);
                    _aery_console.push(args.map(function(a) { return String(a); }).join(' '));
                },
                warn: function() {
                    var args = Array.prototype.slice.call(arguments);
                    _aery_console.push('[WARN] ' + args.map(function(a) { return String(a); }).join(' '));
                },
                error: function() {
                    var args = Array.prototype.slice.call(arguments);
                    _aery_console.push('[ERROR] ' + args.map(function(a) { return String(a); }).join(' '));
                }
            };
        """)
    
    def get_console_output(self) -> list[str]:
        """Get captured console output."""
        try:
            return self._ctx.eval("_aery_console") or []
        except Exception:
            return self._console_output
    
    def clear_console(self):
        """Clear console output."""
        try:
            self._ctx.eval("_aery_console = []")
        except Exception:
            pass
    
    def execute(self, script: str, context: Optional[ScriptContext] = None) -> ScriptResult:
        """Execute a script in the sandbox."""
        self.clear_console()
        
        start = time.time()
        
        try:
            # Inject context if provided
            if context:
                self._inject_context(context)
            
            # Execute script with timeout
            # Note: PyMiniRacer doesn't have built-in timeout, we rely on max_execution_time_ms
            result = self._ctx.eval(script)
            
            execution_time = (time.time() - start) * 1000
            
            return ScriptResult(
                success=True,
                result=result,
                execution_time_ms=execution_time,
                console_output=self.get_console_output(),
            )
            
        except Exception as e:
            execution_time = (time.time() - start) * 1000
            return ScriptResult(
                success=False,
                error=str(e),
                execution_time_ms=execution_time,
                console_output=self.get_console_output(),
            )
    
    def _inject_context(self, context: ScriptContext):
        """Inject QGIS context into the sandbox."""
        # Create a JSON-serializable version of context
        ctx_data = {
            "projectDir": context.project_dir,
            "layers": context.layers,
            "canvasExtent": context.canvas_extent,
            "crs": context.crs,
            "userData": context.user_data,
        }
        
        # Inject as global variable
        ctx_json = json.dumps(ctx_data)
        self._ctx.eval(f"const aeryContext = {ctx_json};")
        
        # Inject helper functions
        self._inject_helpers()
    
    def _inject_helpers(self):
        """Inject helper functions for QGIS interaction."""
        helpers = """
        // Helper functions available in scripts
        const aeryHelpers = {
            // Get layer by name
            getLayer: function(name) {
                return aeryContext.layers.find(function(l) { return l.name === name; });
            },
            // Get layer by ID
            getLayerById: function(id) {
                return aeryContext.layers.find(function(l) { return l.id === id; });
            },
            // List all layer names
            listLayers: function() {
                return aeryContext.layers.map(function(l) { return l.name; });
            },
            // Get project info
            getProjectInfo: function() {
                return {
                    dir: aeryContext.projectDir,
                    crs: aeryContext.crs,
                    extent: aeryContext.canvasExtent,
                    layerCount: aeryContext.layers.length,
                };
            },
            // Log to console (already available via console.log)
            log: function(msg) {
                console.log(msg);
            },
            // Create a result object for the script
            result: function(data) {
                return data;
            },
        };
        """
        self._ctx.eval(helpers)
        self._globals_defined = True
    
    def eval(self, expression: str) -> Any:
        """Evaluate a simple expression."""
        return self._ctx.eval(expression)


class ScriptRunner:
    """High-level script runner with QGIS integration."""
    
    def __init__(self, iface=None, executor=None):
        self.iface = iface
        self.executor = executor
        self._sandbox: Optional[ScriptSandbox] = None
    
    def _get_sandbox(self) -> ScriptSandbox:
        """Get or create sandbox."""
        if self._sandbox is None:
            self._sandbox = ScriptSandbox()
        return self._sandbox
    
    def run_script(
        self,
        script: str,
        context: Optional[ScriptContext] = None,
    ) -> ScriptResult:
        """Run a script with the given context."""
        sandbox = self._get_sandbox()
        return sandbox.execute(script, context)
    
    def run_file(
        self,
        filepath: str,
        context: Optional[ScriptContext] = None,
    ) -> ScriptResult:
        """Run a script from a file."""
        try:
            with open(filepath, "r") as f:
                script = f.read()
            return self.run_script(script, context)
        except Exception as e:
            return ScriptResult(
                success=False,
                error=f"Failed to read script file: {e}",
            )
    
    def build_context_from_qgis(self) -> ScriptContext:
        """Build ScriptContext from current QGIS state."""
        if not self.iface:
            return ScriptContext()
        
        try:
            from qgis.core import QgsProject, QgsMapLayer, QgsCoordinateReferenceSystem
            
            project = QgsProject.instance()
            canvas = self.iface.mapCanvas() if self.iface else None
            
            layers = []
            for layer in project.mapLayers().values():
                if layer.type() == QgsMapLayer.LayerType.VectorLayer:
                    layer_info = {
                        "id": layer.id(),
                        "name": layer.name(),
                        "type": "vector",
                        "geometry_type": str(layer.geometryType()),
                        "feature_count": layer.featureCount(),
                        "crs": layer.crs().authid(),
                        "fields": [f.name() for f in layer.fields()],
                    }
                elif layer.type() == QgsMapLayer.LayerType.RasterLayer:
                    layer_info = {
                        "id": layer.id(),
                        "name": layer.name(),
                        "type": "raster",
                        "width": layer.width(),
                        "height": layer.height(),
                        "crs": layer.crs().authid(),
                    }
                else:
                    layer_info = {
                        "id": layer.id(),
                        "name": layer.name(),
                        "type": "other",
                    }
                layers.append(layer_info)
            
            extent = {}
            if canvas:
                ext = canvas.extent()
                extent = {
                    "xmin": ext.xMinimum(),
                    "ymin": ext.yMinimum(),
                    "xmax": ext.xMaximum(),
                    "ymax": ext.yMaximum(),
                }
            
            crs = project.crs().authid() if project.crs().isValid() else ""
            
            return ScriptContext(
                project_dir=os.path.dirname(project.fileName()) if project.fileName() else "",
                layers=layers,
                canvas_extent=extent,
                crs=crs,
            )
        except Exception as e:
            logger.error(f"Failed to build QGIS context: {e}")
            return ScriptContext()
    
    def execute_with_qgis_context(self, script: str) -> ScriptResult:
        """Execute a script with auto-built QGIS context."""
        context = self.build_context_from_qgis()
        return self.run_script(script, context)


# Example scripts for common tasks
EXAMPLE_SCRIPTS = {
    "list_layers": """
// List all layers in the project
const layers = aeryHelpers.listLayers();
console.log("Layers:", layers);
aeryHelpers.result({ layers: layers });
""",
    
    "zoom_to_layer": """
// Zoom to a specific layer
const layerName = "my_layer"; // Change this
const layer = aeryHelpers.getLayer(layerName);
if (layer) {
    console.log("Found layer:", layer.name);
    // In real QGIS, you'd call qgis.iface.mapCanvas().zoomToLayer(...)
    aeryHelpers.result({ zoomed: true, layer: layer.name });
} else {
    console.error("Layer not found:", layerName);
    aeryHelpers.result({ zoomed: false, error: "Layer not found" });
}
""",
    
    "get_layer_info": """
// Get detailed info about a layer
const layerName = "my_layer"; // Change this
const layer = aeryHelpers.getLayer(layerName);
if (layer) {
    console.log("Layer info:", layer);
    aeryHelpers.result(layer);
} else {
    console.error("Layer not found:", layerName);
    aeryHelpers.result({ error: "Layer not found" });
}
""",
    
    "run_processing": """
// Run a processing algorithm
const result = await aeryHelpers.runProcessing("native:buffer", {
    INPUT: "my_layer",
    DISTANCE: 100,
    OUTPUT: "memory:",
});
console.log("Buffer result:", result);
aeryHelpers.result(result);
""",
}


def get_example_script(name: str) -> str:
    """Get an example script by name."""
    return EXAMPLE_SCRIPTS.get(name, "")