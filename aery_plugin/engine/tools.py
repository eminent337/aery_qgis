import contextlib
import io
import os
import ast

class EvalTool:
    def __init__(self):
        self.globals_dict = {}
        self.locals_dict = {}
        
    def execute(self, code: str) -> str:
        # Validate AST first to ensure safety (mirroring omp's sandbox and our old QGIS sandbox)
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import) or isinstance(node, ast.ImportFrom):
                    module = getattr(node, 'module', None) or (node.names[0].name if node.names else "")
                    if module in ["os", "subprocess", "sys", "shutil"]:
                        return f"SecurityError: Importing '{module}' is strictly prohibited in EvalTool."
        except SyntaxError as e:
            return f"SyntaxError: {e}"

        f = io.StringIO()
        try:
            with contextlib.redirect_stdout(f):
                exec(code, self.globals_dict, self.locals_dict)
            output = f.getvalue()
            return output if output else "Execution successful."
        except Exception as e:
            return f"Execution Error: {e}"

class EditTool:
    def __init__(self):
        self.description = "Modify local files safely by providing exact target substrings and their replacements."
        
    def execute(self, file_path: str, target: str, replacement: str) -> str:
        if not os.path.exists(file_path):
            return f"Error: File {file_path} does not exist."
            
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
            
        if target not in content:
            return "Error: Target string not found in file. Ensure exact match including whitespace."
            
        if content.count(target) > 1:
            return "Error: Target string is not unique. Provide a larger block of text to match exactly."
            
        new_content = content.replace(target, replacement)
        
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(new_content)
            
        lines_changed = len(replacement.splitlines())
        return f"Successfully updated {file_path}. ({lines_changed} lines written)."
