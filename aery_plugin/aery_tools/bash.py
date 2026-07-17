import subprocess
import os

class BashCommandTool:
    """Aery-Style tool for running bash commands directly from the agent."""
    
    name = "bash_command"
    description = "Run an OS-level bash command. Useful for using gdal, pdal, or manipulating the filesystem."
    
    def execute(self, params: dict) -> dict:
        command = params.get("command", "")
        if not command:
            return {"type": "text", "text": "Error: No command provided."}
            
        try:
            # Run the bash command
            result = subprocess.run(
                command, 
                shell=True, 
                capture_output=True, 
                text=True, 
                cwd=os.path.expanduser("~")
            )
            
            output = result.stdout
            if result.stderr:
                output += f"\nSTDERR:\n{result.stderr}"
                
            return {"type": "text", "text": output if output else "Command executed successfully with no output."}
        except Exception as e:
            return {"type": "text", "text": f"Execution failed: {str(e)}"}
