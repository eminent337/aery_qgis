"""Shared AST sandbox logic for the Aery QGIS plugin.

Consolidated from previously duplicated copies in tools.py and qgis_executor.py.
All sandbox checks — forbidden modules, calls, magic attributes, subscript-based
bypasses, and string-construction primitives — live here as a single source of
truth so we fix gaps once, not twice.

NOTE: AST validation is a *guardrail*, not a containment boundary. The runtime
proxy in qgis_executor._make_sandbox_exec_globals is the second layer; together
they block the known-bad patterns but a determined attacker (or a creative LLM)
may find a new path. Treat any code that reaches exec() as semi-trusted.
"""

import ast

# ── Forbidden sets ──────────────────────────────────────────────────────────

# Modules with no legitimate use inside run_qgis_code. Blocked at import time
# and via runtime proxy. Includes:
#   - OS / process control: os, sys, subprocess, shutil, ctypes, fcntl
#   - Filesystem mutation helpers: pathlib, importlib
#   - Network: socket, ssl, asyncio, http, urllib, ftplib, smtplib, telnetlib,
#     poplib, imaplib, nntplib, xmlrpc, asyncore, selectors, select
#   - Concurrency primitives that escape the watchdog: threading, multiprocessing
#   - Deserialization risks: pickle, marshal, shelve, codecs (codecs.lookup is
#     used in some sandbox escapes)
#   - Introspection: ast, code, codeop, dis, inspect, signal, pdb
#   - Terminal/IO: pty, tty, termios, pwd, spwd, grp, resource, crypt
#   - Process/import internals: builtins, importlib, _imp
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

# Builtins that are either dangerous on their own or commonly used to
# bypass the import/call checks (e.g. chr() to construct "os" from integers,
# vars() to reach __dict__ of objects, type() to walk the MRO).
# NOTE: This set is for the AST walker only. The runtime layer
# (qgis_executor._make_builtins_proxy) maintains its own copy of this list
# inside the restricted __builtins__ dict. Keep them in sync.
#
# Trade-off note: bytes/bytearray/memoryview have legitimate uses in QGIS code
# (binary data handling for processing) so they are NOT blocked here. The
# `chr` / `ord` / `format` builtins are the primary string-construction
# bypasses and ARE blocked. A determined attacker can still construct a
# forbidden name by other means — the AST check is one layer, not a wall.
FORBIDDEN_CALLS = frozenset({
    "exec", "eval", "compile", "globals", "locals", "vars",
    "setattr", "delattr", "getattr", "__import__", "type", "dir", "open",
    "chr", "ord", "format", "input",
    "breakpoint", "issubclass", "help", "callable", "exit", "quit"
})

# Magic attributes that grant access to import machinery, frame state, or
# the class hierarchy. Adding to this set blocks BOTH `obj.__x__` and
# `getattr(obj, "__x__")` (the latter is already caught by FORBIDDEN_CALLS).
FORBIDDEN_MAGIC_ATTRS = frozenset({
    "__dict__", "__bases__", "__mro__", "__base__",
    "__subclasses__", "__getattribute__", "__init_subclass__", "__reduce__",
    "__globals__", "__builtins__", "__loader__", "__spec__", "__path__",
    "__code__", "__closure__", "__defaults__", "__kwdefaults__",
    "__qualname__", "__module__", "__doc__", "__annotations__"
})

# ── Public helpers ──────────────────────────────────────────────────────────

def _module_root(name: str) -> str:
    """Return the top-level package of a dotted import path."""
    return name.split(".", 1)[0]


def check_ast(code: str) -> list[str]:
    """Inspect *code* with the AST walker and return a list of violations.

    Each violation is a human-readable message string.  An empty list means
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
            # level > 0 is a relative import — block outright to keep the
            # import surface auditable
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
            # Catch `os.__dict__["system"]` and `__builtins__["exec"]` style
            # bypasses. The slice value is usually a string literal; if so,
            # reject if it indexes a forbidden name.
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
