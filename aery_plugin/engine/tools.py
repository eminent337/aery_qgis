import contextlib
import io

class EvalTool:
    def __init__(self):
        self.globals_dict = {}
        self.locals_dict = {}
        
    def execute(self, code: str) -> str:
        f = io.StringIO()
        try:
            with contextlib.redirect_stdout(f):
                exec(code, self.globals_dict, self.locals_dict)
            output = f.getvalue()
            return output if output else "Success"
        except Exception as e:
            return f"Error: {e}"
