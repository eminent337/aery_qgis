class QgisProcessingTool:
    """Tool that allows the agent to discover and run native QGIS Processing Algorithms."""
    
    name = "run_qgis_algorithm"
    description = "Run any native QGIS processing algorithm (e.g., native:buffer, native:intersection). If you do not know the exact algorithm name, you should search the web or write a python script to list algorithms using QgsApplication.processingRegistry()."
    
    def execute(self, params: dict) -> dict:
        alg_id = params.get("alg_id", "")
        alg_params = params.get("params", {})
        
        if not alg_id:
            return {"type": "text", "text": "Error: alg_id is required."}
            
        try:
            import processing
            from qgis.core import QgsApplication
            
            # Verify algorithm exists
            registry = QgsApplication.processingRegistry()
            alg = registry.algorithmById(alg_id)
            if not alg:
                return {"type": "text", "text": f"Error: Algorithm '{alg_id}' not found in QGIS."}
            
            # Execute
            result = processing.run(alg_id, alg_params)
            
            output = f"Algorithm '{alg_id}' executed successfully.\nResults:\n"
            for key, value in result.items():
                output += f"- {key}: {value}\n"
                
            return {"type": "text", "text": output}
            
        except Exception as e:
            return {"type": "text", "text": f"Processing Error: {str(e)}"}
