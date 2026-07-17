import traceback

class PythonExecutorTool:
    """Tool that allows the agent to write and execute custom PyQGIS scripts."""
    
    name = "execute_python"
    description = "Execute arbitrary Python code within the QGIS environment. Use this to interact with QGIS APIs, layers, create custom geoprocessing tools, or perform tasks when a native tool doesn't exist."
    
    def __init__(self, iface):
        self.iface = iface

    def execute(self, params: dict) -> dict:
        code = params.get("code", "")
        if not code:
            return {"type": "text", "text": "Error: No python code provided."}
            
        # Create a safe execution environment with QGIS context
        local_env = {"iface": self.iface}
        global_env = {}
        
        try:
            # We redirect stdout to capture print() statements from the script
            import sys, io
            old_stdout = sys.stdout
            redirected_output = sys.stdout = io.StringIO()
            
            # Execute the agent's code
            exec(code, global_env, local_env)
            
            # Restore stdout
            sys.stdout = old_stdout
            
            output = redirected_output.getvalue()
            return {"type": "text", "text": f"Code executed successfully.\nOutput:\n{output}" if output else "Code executed successfully with no output."}
        except Exception as e:
            # Restore stdout in case of crash
            import sys
            sys.stdout = sys.__stdout__
            error_trace = traceback.format_exc()
            return {"type": "text", "text": f"Python Execution Error:\n{error_trace}"}
