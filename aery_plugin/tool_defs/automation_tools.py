TOOLS = [
    {
        "name": "batch_process",
        "description": "Apply a QGIS processing algorithm to all files matching a glob pattern",
        "parameters": {
            "type": "object",
            "properties": {
                "algorithm_id": {"type": "string", "description": "Processing algorithm ID (e.g. 'native:buffer')"},
                "input_pattern": {"type": "string", "description": "Glob pattern to match input files (e.g. '/path/to/*.shp')"},
                "file_field": {"type": "string", "description": "Parameter name to pass each file path into when set"},
                "params": {"type": "object", "description": "Additional algorithm parameters", "additionalProperties": True},
                "output_dir": {"type": "string", "description": "Output directory for result files"}
            },
            "required": ["algorithm_id", "input_pattern", "output_dir"]
        },
        "code": """from qgis.core import QgsProject, QgsVectorLayer, QgsRasterLayer
from aery_plugin.logger import logger
import processing
import glob
import os

algorithm_id = {algorithm_id}
input_pattern = {input_pattern}
file_field = {file_field}
user_params = {params}
output_dir = {output_dir}

os.makedirs(output_dir, exist_ok=True)
matching = glob.glob(input_pattern)
if not matching:
    raise RuntimeError(f"No files match pattern: {input_pattern}")

processed = []
for fp in matching:
    base = os.path.splitext(os.path.basename(fp))[0]
    out_path = os.path.join(output_dir, f'{base}_output.gpkg')
    run_params = dict(user_params) if user_params else {{}}
    if file_field:
        run_params[file_field] = fp
    run_params['OUTPUT'] = out_path
    processing.run(algorithm_id, run_params)
    lyr = QgsVectorLayer(out_path, base, 'ogr')
    if lyr.isValid():
        QgsProject.instance().addMapLayer(lyr)
    else:
        lyr = QgsRasterLayer(out_path, base)
        if lyr.isValid():
            QgsProject.instance().addMapLayer(lyr)
        else:
            raise RuntimeError(f"Failed to load result for {base}")
    processed.append(base)

iface.mapCanvas().refresh()
result = f"Batch processed {len(processed)} files: {', '.join(processed)}"
"""
    },
    {
        "name": "run_model",
        "description": "Execute a QGIS .model3 processing model and load its outputs",
        "parameters": {
            "type": "object",
            "properties": {
                "model_path": {"type": "string", "description": "Full path to the .model3 file"},
                "params": {"type": "object", "description": "Model input parameters", "additionalProperties": True},
                "output_name": {"type": "string", "description": "Display name for the output layer(s)"}
            },
            "required": ["model_path", "output_name"]
        },
        "code": """from qgis.core import QgsProject, QgsVectorLayer, QgsRasterLayer
import processing
import os

model_path = {model_path}
user_params = {params}
output_name = {output_name}

if not os.path.exists(model_path):
    raise RuntimeError(f"Model file not found: {model_path}")

run_params = dict(user_params) if user_params else {{}}
alias = f"model:{model_path}"
result_obj = processing.run(alias, run_params)

loaded_any = False
for key, val in result_obj.items():
    if hasattr(val, 'isValid'):
        if val.isValid():
            name = output_name if not loaded_any else f'{output_name}_{key}'
            val.setName(name)
            QgsProject.instance().addMapLayer(val)
            loaded_any = True
    elif isinstance(val, str) and os.path.exists(val):
        name = output_name if not loaded_any else f'{output_name}_{key}'
        lyr = QgsVectorLayer(val, name, 'ogr')
        if not lyr.isValid():
            lyr = QgsRasterLayer(val, name)
        if lyr.isValid():
            QgsProject.instance().addMapLayer(lyr)
            loaded_any = True

if not loaded_any:
    raise RuntimeError("Model executed but no valid output layers were produced")

iface.mapCanvas().refresh()
result = f"Model executed successfully: {output_name}"
"""
    },
    {
        "name": "schedule_task",
        "description": "Schedule Python code to execute once in the background after a delay, without blocking the QGIS UI",
        "parameters": {
            "type": "object",
            "properties": {
                "task_name": {"type": "string", "description": "A descriptive name for the background task"},
                "code": {"type": "string", "description": "Python code to execute when the timer fires"},
                "interval_seconds": {"type": "integer", "description": "Delay in seconds before the code runs"}
            },
            "required": ["task_name", "code", "interval_seconds"]
        },
        "code": """from qgis.core import QgsProject
from PyQt6.QtCore import QTimer
import processing

task_name = {task_name}
task_code = {code}
interval = {interval_seconds}

def _run_task():
    try:
        exec(task_code)
        logger.info(f"[schedule_task] '{task_name}' completed successfully")
    except Exception as e:
        logger.info(f"[schedule_task] '{task_name}' failed: {e}")

QTimer.singleShot(interval * 1000, _run_task)
result = f"Scheduled '{task_name}' to run in {interval} second(s)"
"""
    },
]
