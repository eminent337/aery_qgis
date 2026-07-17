"""Layer management tools for the Aery QGIS agent.

Each tool entry is a dict with name, description, parameters (JSON Schema),
and code (a Python string with {param_name} placeholders replaced at runtime).
"""

TOOLS = [
    {
        "name": "set_layer_visibility",
        "description": (
            "Show or hide a layer in the QGIS layer tree. "
            "Use this to toggle layer visibility on the map canvas."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "layer_name": {
                    "type": "string",
                    "description": "Name of the layer to show or hide",
                },
                "visible": {
                    "type": "boolean",
                    "description": "True to show the layer, False to hide it",
                },
            },
            "required": ["layer_name", "visible"],
        },
        "code": """from qgis.core import QgsProject
project = QgsProject.instance()
layer = next((l for l in project.mapLayers().values() if l.name() == {layer_name}), None)
if layer is None:
    raise ValueError("Layer not found: " + str({layer_name}))
root = project.layerTreeRoot()
tl = root.findLayer(layer.id())
if tl:
    tl.setItemVisibilityChecked({visible})
iface.mapCanvas().refresh()
result = {"layer": layer.name(), "visible": {visible}}""",
    },
    {
        "name": "reorder_layers",
        "description": (
            "Move a layer to a specific position in the QGIS layer panel. "
            "Positions start at 0 (top of the layer tree)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "layer_name": {
                    "type": "string",
                    "description": "Name of the layer to move",
                },
                "position": {
                    "type": "integer",
                    "description": "Target index in the layer tree (0 = top)",
                },
            },
            "required": ["layer_name", "position"],
        },
        "code": """from qgis.core import QgsProject
project = QgsProject.instance()
layer = next((l for l in project.mapLayers().values() if l.name() == {layer_name}), None)
if layer is None:
    raise ValueError("Layer not found: " + str({layer_name}))
root = project.layerTreeRoot()
tl = root.findLayer(layer.id())
if tl is None:
    raise ValueError("Layer not visible in tree")
parent = tl.parent()
clone = tl.clone()
idx = min({position}, parent.children().count())
parent.insertChildNode(idx, clone)
parent.removeChildNode(tl)
iface.mapCanvas().refresh()
result = {"layer": layer.name(), "position": {position}}""",
    },
    {
        "name": "group_layers",
        "description": (
            "Create a new layer group in the QGIS layer tree and organise "
            "specified layers into it. Useful for keeping the project tidy."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "group_name": {
                    "type": "string",
                    "description": "Name for the new group",
                },
                "layers": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of layer names to move into the group",
                },
            },
            "required": ["group_name", "layers"],
        },
        "code": """from qgis.core import QgsProject
project = QgsProject.instance()
root = project.layerTreeRoot()
group = root.insertGroup(0, {group_name})
moved = []
for name in {layers}:
    layer = next((l for l in project.mapLayers().values() if l.name() == name), None)
    if layer is None:
        continue
    tl = root.findLayer(layer.id())
    if tl:
        clone = tl.clone()
        group.addChildNode(clone)
        root.removeChildNode(tl)
        moved.append(name)
iface.mapCanvas().refresh()
result = {"group": {group_name}, "moved": moved}""",
    },
    {
        "name": "confirm_action",
        "description": (
            "Prompt the user for confirmation before performing a destructive "
            "operation. Displays a dialog with the provided message."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Confirmation message to display to the user",
                },
            },
            "required": ["message"],
        },
        "code": """from PyQt6.QtWidgets import QMessageBox
msg = {message}
reply = QMessageBox.question(None, "Confirm", msg, QMessageBox.Yes | QMessageBox.No)
confirmed = reply == QMessageBox.Yes
result = {"confirmed": confirmed, "message": msg}""",
    },
    {
        "name": "get_audit_trail",
        "description": (
            "Read the .aery/operations.jsonl audit log from the current QGIS "
            "project directory. Returns all recorded operations with timestamps."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
        },
        "code": """import os
import json
from qgis.core import QgsProject
project = QgsProject.instance()
project_dir = os.path.dirname(project.fileName()) if project.fileName() else os.path.expanduser("~")
audit_path = os.path.join(project_dir, ".aery", "operations.jsonl")
records = []
if os.path.exists(audit_path):
    with open(audit_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
result = {"path": audit_path, "count": len(records), "records": records}""",
    },
]
