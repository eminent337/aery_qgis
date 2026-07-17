"""Tests for the runtime module proxies in qgis_executor.

These proxies are the second layer of the sandbox: even if the AST check
is bypassed, dangerous module access raises RuntimeError. Tests cover
the known-bad paths: `os.system`, `subprocess.Popen`, `shutil.rmtree`,
`urllib.request`, and the magic-attribute / subscript escapes.
"""
import pytest

from aery_plugin.qgis_executor import (
    _make_os_proxy,
    _make_subprocess_proxy,
    _make_shutil_proxy,
    _make_urllib_proxy,
    _make_builtins_proxy,
    _make_sandbox_exec_globals,
)


# ── os proxy ──────────────────────────────────────────────────────────────

def test_os_proxy_blocks_system_call():
    proxy = _make_os_proxy()
    with pytest.raises(RuntimeError, match="forbidden"):
        proxy.system("echo hi")


def test_os_proxy_blocks_subprocess_exec_variants():
    proxy = _make_os_proxy()
    for fn in ("popen", "execl", "execle", "execlp", "execlpe",
               "execv", "execve", "execvp", "execvpe", "fork", "kill"):
        with pytest.raises(RuntimeError, match="forbidden"):
            getattr(proxy, fn)()


def test_os_proxy_blocks_filesystem_deletion():
    proxy = _make_os_proxy()
    for fn in ("remove", "unlink", "rmdir", "removedirs"):
        with pytest.raises(RuntimeError, match="forbidden"):
            getattr(proxy, fn)("/tmp/foo")


def test_os_proxy_blocks_dict_access():
    """`os.__dict__["system"]` is the standard sandbox escape — must be blocked."""
    proxy = _make_os_proxy()
    with pytest.raises(RuntimeError, match="forbidden"):
        _ = proxy.__dict__


def test_os_proxy_blocks_class_access():
    proxy = _make_os_proxy()
    with pytest.raises(RuntimeError, match="forbidden"):
        _ = proxy.__class__


def test_os_proxy_blocks_subscript_access():
    proxy = _make_os_proxy()
    with pytest.raises(RuntimeError, match="forbidden"):
        _ = proxy["system"]


def test_os_proxy_allows_safe_access():
    """Names like `sep`, `getcwd`, `path` should still be reachable."""
    proxy = _make_os_proxy()
    assert proxy.sep  # '/' on linux
    # getcwd is a safe passthrough to the real os
    import os
    assert proxy.getcwd() == os.getcwd()


# ── subprocess proxy ──────────────────────────────────────────────────────

def test_subprocess_proxy_blocks_all_exec():
    proxy = _make_subprocess_proxy()
    for fn in ("Popen", "run", "call", "check_output", "check_call",
               "getoutput", "getstatusoutput"):
        with pytest.raises(RuntimeError, match="forbidden"):
            getattr(proxy, fn)("echo hi", shell=True)


def test_subprocess_proxy_blocks_dict_access():
    proxy = _make_subprocess_proxy()
    with pytest.raises(RuntimeError, match="forbidden"):
        _ = proxy.__dict__


def test_subprocess_proxy_blocks_class_access():
    proxy = _make_subprocess_proxy()
    with pytest.raises(RuntimeError, match="forbidden"):
        _ = proxy.__class__


# ── shutil proxy ──────────────────────────────────────────────────────────

def test_shutil_proxy_blocks_rmtree():
    proxy = _make_shutil_proxy()
    with pytest.raises(RuntimeError, match="forbidden"):
        proxy.rmtree("/tmp/foo")


def test_shutil_proxy_blocks_move():
    proxy = _make_shutil_proxy()
    with pytest.raises(RuntimeError, match="forbidden"):
        proxy.move("/tmp/a", "/tmp/b")


def test_shutil_proxy_blocks_dict_access():
    proxy = _make_shutil_proxy()
    with pytest.raises(RuntimeError, match="forbidden"):
        _ = proxy.__dict__


# ── urllib proxy ──────────────────────────────────────────────────────────

def test_urllib_proxy_blocks_request():
    """`urllib.request` can open sockets — must be blocked."""
    proxy = _make_urllib_proxy()
    with pytest.raises(RuntimeError, match="network access blocked"):
        _ = proxy.request


def test_urllib_proxy_blocks_error():
    """`urllib.error` is part of the request machinery."""
    proxy = _make_urllib_proxy()
    with pytest.raises(RuntimeError, match="network access blocked"):
        _ = proxy.error


def test_urllib_proxy_blocks_dict_access():
    proxy = _make_urllib_proxy()
    with pytest.raises(RuntimeError, match="forbidden"):
        _ = proxy.__dict__


def test_urllib_proxy_allows_parse():
    """URL building/parsing is a legitimate QGIS use case (WMS URIs)."""
    proxy = _make_urllib_proxy()
    parse = proxy.parse
    # urllib.parse.quote is the canonical URL-encoding function
    assert parse.quote("hello world") == "hello%20world"
    assert parse.urlencode({"key": "value"}) == "key=value"


def test_urllib_parse_proxy_blocks_dict_access():
    proxy = _make_urllib_proxy()
    parse = proxy.parse
    with pytest.raises(RuntimeError, match="forbidden"):
        _ = parse.__dict__


# ── builtins proxy ────────────────────────────────────────────────────────

def test_builtins_proxy_omits_execution_builtins():
    safe = _make_builtins_proxy()
    for name in ("exec", "eval", "compile", "getattr",
                 "setattr", "chr", "ord", "format", "open", "input",
                 "vars", "globals", "locals", "breakpoint"):
        assert name not in safe, f"{name!r} should not be in restricted builtins"


def test_builtins_proxy_keeps_safe_builtins():
    safe = _make_builtins_proxy()
    for name in ("len", "range", "print", "list", "dict", "str", "int",
                 "float", "sum", "min", "max", "sorted", "enumerate",
                 "zip", "map", "filter", "abs", "round"):
        assert name in safe, f"{name!r} should be in restricted builtins"


# ── _make_sandbox_exec_globals integration ────────────────────────────────

def test_sandbox_globals_replaces_dangerous_modules():
    """The factory must replace os/subprocess/shutil/urllib with proxies."""
    base = {
        "os": "fake-os",
        "subprocess": "fake-subprocess",
        "shutil": "fake-shutil",
        "urllib": "fake-urllib",
        "ctypes": "fake-ctypes",  # should be stripped
        "socket": "fake-socket",  # should be stripped
        "importlib": "fake-importlib",  # should be stripped
        "qgis_core": "qgis",
    }
    g = _make_sandbox_exec_globals(base, "/tmp")

    # Module references must be replaced with proxy objects
    assert g["os"] is not "fake-os"
    assert g["subprocess"] is not "fake-subprocess"
    assert g["shutil"] is not "fake-shutil"
    assert g["urllib"] is not "fake-urllib"

    # Stripped modules must be absent
    assert "ctypes" not in g
    assert "socket" not in g
    assert "importlib" not in g

    # Benign globals must be preserved
    assert g["qgis_core"] == "qgis"

    # __builtins__ must be a dict (required by exec)
    assert isinstance(g["__builtins__"], dict)


def test_sandbox_globals_replaced_proxies_actually_block():
    """End-to-end: code that calls os.system() in the sandbox raises."""
    base = {"__name__": "__main__"}
    g = _make_sandbox_exec_globals(base, "/tmp")
    local_vars: dict = {}

    # Path 2: proxy is the `os` global — calling .system() raises RuntimeError
    with pytest.raises(RuntimeError, match="forbidden"):
        exec("os.system('echo hi')", g, local_vars)


def test_sandbox_globals_blocks_import_os_at_runtime():
    """The AST block is the primary check, but the proxy should also block
    the function if AST is somehow bypassed (e.g. pre-imported os)."""
    base = {"__name__": "__main__"}
    g = _make_sandbox_exec_globals(base, "/tmp")
    local_vars: dict = {}
    # Simulate AST being bypassed by using exec with the proxy directly
    with pytest.raises(RuntimeError, match="forbidden"):
        exec("os.system('echo hi')", g, local_vars)
# ── single-namespace exec (executor P2 Step 1 fix) ───────────────────────
def test_single_namespace_exec_blocks_import_os_system():
    """Regression for P2 Step 1: the executor now calls exec(code, sandbox_g)
    with a SINGLE namespace (no separate locals dict). `import os` must still
    resolve to the proxy (via the overridden __import__), and os.system() must
    raise — proving the name never escapes to the real module."""
    base = {"__name__": "__main__"}
    g = _make_sandbox_exec_globals(base, "/tmp")
    # Mirror the executor: inject locals into the ONE globals namespace.
    g.update({"result": None, "iface": None, "project_dir": "/tmp"})
    exec("import os", g)
    # os is the proxy, not the real module
    import os as real_os
    assert g["os"] is not real_os
    with pytest.raises(RuntimeError, match="forbidden"):
        exec("os.system('echo hi')", g)
def test_single_namespace_exec_captures_result_variable():
    """The executor reads `result` back from the single sandbox namespace
    after exec. Verify user-assigned result is retrievable (no separate
    locals dict to lose it in)."""
    base = {"__name__": "__main__"}
    g = _make_sandbox_exec_globals(base, "/tmp")
    g.update({"result": None})
    exec("result = 2 + 3", g)
    assert g["result"] == 5
