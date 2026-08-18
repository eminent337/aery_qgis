"""Runtime sandbox proxies for QGIS code execution.

These proxies are the second layer of defense in the sandbox: even if the AST
check is bypassed, dangerous module access raises RuntimeError.

The executor injects these proxies into the globals dict before exec.
"""

from __future__ import annotations

import builtins
import os
from typing import Any, Optional


# ── Forbidden attribute sets ────────────────────────────────────────────────

_OS_FORBIDDEN = {
    "system", "popen", "popen2", "popen3", "popen4",
    "execl", "execle", "execlp", "execlpe",
    "execv", "execve", "execvp", "execvpe",
    "fork", "fork1", "kill", "killpg",
    "remove", "unlink", "rmdir", "removedirs", "rmtree",
    "rename", "renames", "replace",
    "truncate", "fdopen", "dup", "dup2",
}

_SUBPROCESS_FORBIDDEN = {
    "Popen", "call", "check_call", "check_output", "run",
    "create_subprocess_shell", "create_subprocess_exec",
    "getoutput", "getstatusoutput",
}

_SHUTIL_FORBIDDEN = {
    "rmtree", "rmtree_avoid_bug", "move", "copy", "copy2", "copyfile",
    "copymode", "copystat", "copytree", "disk_usage",
    "chown", "which", "get_archive_formats", "make_archive",
}

_URLLIB_FORBIDDEN = {"request", "error", "response", "robotparser"}

# Modules to strip from sandbox globals entirely
_SANDBOX_BLACKLIST = {"ctypes", "socket", "importlib", "pty", "telnetlib"}

# Builtins that must NOT appear in the sandbox (dangerous for sandboxed code)
_FORBIDDEN_BUILTINS = frozenset({
    "exec", "eval", "compile",
    "getattr", "setattr", "delattr",
    "chr", "ord", "format", "open", "input",
    "vars", "globals", "locals", "breakpoint",
})

# Builtins that are safe and helpful in sandboxed code
_SAFE_BUILTINS = {
    "abs", "all", "any", "bool", "bytearray", "bytes", "dict",
    "divmod", "enumerate", "filter", "float", "frozenset",
    "hash", "hex", "id", "int", "isinstance", "issubclass",
    "iter", "len", "list", "map", "max", "memoryview",
    "min", "next", "oct", "pow", "print", "property",
    "range", "repr", "reversed", "round", "set",
    "slice", "sorted", "str", "sum", "tuple", "type",
    "zip", "True", "False", "None", "Ellipsis",
    "NotImplemented",
}


# ── Proxy classes ───────────────────────────────────────────────────────────

class _SandboxProxy:
    """A module proxy that raises RuntimeError on forbidden attributes.

    Internal fields (_module, _forbidden) are set via object.__setattr__
    to bypass our own __setattr__ guard.
    """

    _forbidden: set = set()
    _module: Any = None

    def __getattr__(self, name: str) -> Any:
        if name in self._forbidden:
            raise RuntimeError(f"'{name}' is forbidden in sandbox execution")
        attr = getattr(self._module, name, None)
        if attr is not None:
            return attr
        raise AttributeError(f"module '{self._module.__name__}' has no attribute '{name}'")

    def __setattr__(self, name: str, value: Any) -> None:
        raise RuntimeError("module mutation is forbidden in sandbox execution")

    def __delattr__(self, name: str) -> None:
        raise RuntimeError("module mutation is forbidden in sandbox execution")

    def __getitem__(self, key: str) -> Any:
        raise RuntimeError("dict subscript access is forbidden in sandbox execution")

    def __setitem__(self, key: str, value: Any) -> None:
        raise RuntimeError("module mutation is forbidden in sandbox execution")

    @property
    def __class__(self):
        raise RuntimeError("class introspection is forbidden in sandbox execution")

    @property
    def __dict__(self):
        raise RuntimeError("dict access is forbidden in sandbox execution")


def _make_proxy(module: Any, forbidden: set) -> _SandboxProxy:
    """Create a sandbox proxy for a module."""
    proxy = object.__new__(_SandboxProxy)
    object.__setattr__(proxy, "_module", module)
    object.__setattr__(proxy, "_forbidden", frozenset(forbidden))
    return proxy


def _make_os_proxy() -> _SandboxProxy:
    """Create a sandboxed proxy for the os module."""
    return _make_proxy(os, _OS_FORBIDDEN)


def _make_subprocess_proxy() -> _SandboxProxy:
    """Create a sandboxed proxy for the subprocess module."""
    import subprocess
    return _make_proxy(subprocess, _SUBPROCESS_FORBIDDEN)


def _make_shutil_proxy() -> _SandboxProxy:
    """Create a sandboxed proxy for the shutil module."""
    import shutil
    return _make_proxy(shutil, _SHUTIL_FORBIDDEN)


class _UrllibProxy(_SandboxProxy):
    """Proxy for urllib that blocks dangerous submodules but allows safe ones."""

    def __getattr__(self, name: str) -> Any:
        if name in _URLLIB_FORBIDDEN:
            raise RuntimeError("network access blocked: urllib submodules are forbidden")
        if name in ("parse",):
            # urllib.parse is safe — allow it but wrap to block dict access
            return _UrllibParseProxy(getattr(self._module, name))
        return super().__getattr__(name)


class _UrllibParseProxy:
    """Proxy for urllib.parse that blocks __dict__/__class__ access."""

    def __init__(self, parse_module):
        object.__setattr__(self, "_parse_module", parse_module)

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise RuntimeError("private attribute access is forbidden")
        return getattr(self._parse_module, name)

    def __setattr__(self, name: str, value: Any) -> None:
        raise RuntimeError("module mutation is forbidden in sandbox execution")

    def __getitem__(self, key: str) -> Any:
        raise RuntimeError("dict subscript access is forbidden in sandbox execution")

    @property
    def __class__(self):
        raise RuntimeError("class introspection is forbidden in sandbox execution")

    @property
    def __dict__(self):
        raise RuntimeError("dict access is forbidden in sandbox execution")


def _make_urllib_proxy() -> _SandboxProxy:
    """Create a sandboxed proxy for urllib (blocks urllib.request)."""
    import urllib
    proxy = object.__new__(_UrllibProxy)
    object.__setattr__(proxy, "_module", urllib)
    object.__setattr__(proxy, "_forbidden", frozenset(_URLLIB_FORBIDDEN))
    return proxy


# ── Builtins proxy ──────────────────────────────────────────────────────────

def _make_builtins_proxy() -> dict:
    """Return a restricted __builtins__ dict for sandbox exec.

    Removes dangerous builtins like __import__, exec, eval, compile,
    getattr, setattr, open, etc.

    A custom __import__ is injected so that ``import os`` in the sandbox
    resolves to the proxy object rather than the real module, maintaining
    the defense-in-depth guarantee even with the single-namespace exec
    pattern used by the executor.
    """
    safe: dict = {}
    for name in _SAFE_BUILTINS:
        if hasattr(builtins, name):
            safe[name] = getattr(builtins, name)
    return safe


def _make_sandbox_exec_globals(
    custom_globals: Optional[dict] = None,
    project_dir: Optional[str] = None,
) -> dict[str, Any]:
    """Build a restricted globals dict for exec().

    Args:
        custom_globals: Optional dict of variables to merge in (e.g. iface, result).
        project_dir: Optional project directory for context.

    Replaces dangerous modules (os, subprocess, shutil, urllib) with sandbox
    proxies. Strips blacklisted modules (ctypes, socket, importlib, pty,
    telnetlib). Preserves benign globals from custom_globals.
    """
    os_proxy = _make_os_proxy()

    # Custom __import__ that always returns the proxy for os, preventing
    # import-based escape. For all other modules, falls through to real import
    # but those are already stripped/Blocked by AST check.
    def _safe_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "os":
            return os_proxy
        # For any other module, use the real __import__ but the AST check
        # should have already blocked it. This is defense-in-depth.
        return _real_import(name, globals, locals, fromlist, level)

    _real_import = builtins.__import__

    g: dict[str, Any] = {
        "__builtins__": _make_builtins_proxy(),
        "os": os_proxy,
        "subprocess": _make_subprocess_proxy(),
        "shutil": _make_shutil_proxy(),
        "urllib": _make_urllib_proxy(),
    }
    # Inject the safe import into builtins so `import os` resolves to proxy
    g["__builtins__"]["__import__"] = _safe_import

    if project_dir is not None:
        g["project_dir"] = project_dir

    if custom_globals:
        for key, value in custom_globals.items():
            if key in _SANDBOX_BLACKLIST:
                continue  # strip blacklisted modules
            if key in ("os", "subprocess", "shutil", "urllib"):
                continue  # already set to proxies
            g[key] = value
    return g
# ── AST Sandbox Checker (first layer of defense) ────────────────────────────
import ast
FORBIDDEN_MODULES = frozenset({
    "socket", "builtins", "http", "http.client",
    "importlib", "ctypes", "fcntl", "termios", "tty", "pty",
    "_imp", "subprocess", "shutil", "urllib", "requests", "httpx", "aiohttp",
    "threading", "multiprocessing", "signal", "atexit",
    "asyncio", "select", "selectors", "asyncore",
    "ssl", "ftplib", "poplib", "imaplib",
    "nntplib", "smtplib", "telnetlib", "xmlrpc", "xmlrpc.client", "xmlrpc.server",
    "socketserver",
    "pickle", "marshal", "shelve", "codecs", "plistlib",
    "ast", "code", "codeop", "dis", "inspect", "pdb",
    "pwd", "spwd", "grp", "resource", "crypt",
    "pty", "posix", "posixpath", "nt", "ntpath", "win32api", "win32com",
})
# Forbidden function calls
FORBIDDEN_CALLS = frozenset({
    "exec", "eval", "compile", "globals", "locals", "vars",
    "setattr", "delattr", "getattr", "__import__", "type", "dir", "open",
    "chr", "ord", "format", "input",
    "breakpoint", "issubclass", "help", "callable", "exit", "quit"
})
# Forbidden magic attributes
FORBIDDEN_MAGIC_ATTRS = frozenset({
    "__dict__", "__bases__", "__mro__", "__base__",
    "__subclasses__", "__getattribute__", "__init_subclass__", "__reduce__",
    "__globals__", "__builtins__", "__loader__", "__spec__", "__path__",
    "__code__", "__closure__", "__defaults__", "__kwdefaults__",
    "__qualname__", "__module__", "__doc__", "__annotations__"
})
def _module_root(name: str) -> str:
    """Return the top-level package of a dotted import path."""
    return name.split(".", 1)[0]
def check_ast(code: str) -> list[str]:
    """Inspect *code* with the AST walker and return a list of violations.
    Each violation is a human-readable message string. An empty list means
    the code passed all checks (not *safe*, just *free of known AST-detectable
    violations* — the runtime sandbox handles the rest).
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        return [f"code has syntax errors: line {e.lineno}: {e.msg}"]
    violations: list[str] = []
    has_star_import = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "*":
                    has_star_import = True
                    continue
                root = _module_root(alias.name)
                if root in FORBIDDEN_MODULES:
                    violations.append(
                        f"importing forbidden module '{alias.name}'"
                    )
        elif isinstance(node, ast.ImportFrom):
            if any(a.name == "*" for a in node.names):
                has_star_import = True
            if node.module:
                root = _module_root(node.module)
                if root in FORBIDDEN_MODULES:
                    violations.append(
                        f"importing from forbidden module '{node.module}'"
                    )
            if getattr(node, "level", 0) and node.level > 0:
                violations.append("relative imports are forbidden")
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name) and func.id in FORBIDDEN_CALLS:
                violations.append(
                    f"calling forbidden function '{func.id}'"
                )
            elif isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_CALLS:
                violations.append(
                    f"calling forbidden attribute '{func.attr}'"
                )
        elif isinstance(node, ast.Attribute):
            if node.attr in FORBIDDEN_MAGIC_ATTRS:
                violations.append(
                    f"accessing forbidden magic attribute '{node.attr}'"
                )
        elif isinstance(node, ast.Subscript):
            value = node.value
            slc = node.slice
            slc_val = getattr(slc, "value", None) if isinstance(slc, ast.Subscript) else (
                slc if isinstance(slc, ast.Constant) and isinstance(slc.value, str) else None
            )
            if isinstance(value, ast.Name) and value.id == "__builtins__":
                violations.append("subscript access on __builtins__ is forbidden")
            if isinstance(value, ast.Attribute) and value.attr == "__dict__":
                violations.append("subscript access on __dict__ is forbidden")
            if slc_val is not None and isinstance(slc_val, ast.Constant) and isinstance(slc_val.value, str):
                if slc_val.value in FORBIDDEN_CALLS or slc_val.value in FORBIDDEN_MAGIC_ATTRS:
                    violations.append(
                        f"indexing forbidden name '{slc_val.value}'"
                    )
    if has_star_import:
        violations.append("star imports are forbidden")
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for v in violations:
        if v not in seen:
            seen.add(v)
            unique.append(v)
    return unique
def raise_on_violation(code: str, label: str = "code") -> None:
    """Run *check_ast* and raise ``RuntimeError`` on the first violation.
    *label* is used in the error message to describe the origin (e.g. tool
    name or execution context).
    """
    violations = check_ast(code)
    if violations:
        msg = f"Sandbox violation in {label}: {violations[0]}"
        raise RuntimeError(msg)
