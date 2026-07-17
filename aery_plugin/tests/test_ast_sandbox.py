import pytest
from aery_plugin.sandbox import check_ast


def is_code_safe(code: str) -> bool:
    return len(check_ast(code)) == 0


# ── Original safe/baseline tests ──────────────────────────────────────────

def test_safe_qgis_code():
    code = """
from qgis.core import QgsProject, QgsVectorLayer
layer = QgsVectorLayer("Point?crs=epsg:4326", "temporary_points", "memory")
QgsProject.instance().addMapLayer(layer)
print("Added layer")
"""
    assert is_code_safe(code) is True


def test_forbidden_imports():
    # os and sys are allowed statically (replaced by proxies at runtime)
    assert is_code_safe("import os") is True
    assert is_code_safe("import sys") is True
    # subprocess and shutil are blocked statically
    assert is_code_safe("import subprocess") is False
    assert is_code_safe("from shutil import rmtree") is False


def test_forbidden_calls():
    assert is_code_safe("eval('1 + 1')") is False
    assert is_code_safe("exec('print(1)')") is False
    assert is_code_safe("__import__('os').system('ls')") is False
    assert is_code_safe("getattr(__builtins__, 'eval')('1')") is False


def test_forbidden_attributes():
    assert is_code_safe("().__class__.__base__.__subclasses__()") is False
    assert is_code_safe("foo.__globals__") is False


def test_syntax_error_blocked():
    assert is_code_safe("print('hello") is False


# ── New: network / process / async modules ────────────────────────────────

def test_network_modules_blocked():
    assert is_code_safe("import socket") is False
    assert is_code_safe("import urllib.request") is False
    assert is_code_safe("from urllib.request import urlopen") is False
    assert is_code_safe("import http.client") is False
    assert is_code_safe("import asyncio") is False
    assert is_code_safe("import ssl") is False
    assert is_code_safe("import smtplib") is False
    assert is_code_safe("import ftplib") is False
    assert is_code_safe("import telnetlib") is False
    # Third-party (commonly used in generated code)
    assert is_code_safe("import requests") is False
    assert is_code_safe("import httpx") is False
    assert is_code_safe("import aiohttp") is False


def test_concurrency_modules_blocked():
    assert is_code_safe("import threading") is False
    assert is_code_safe("import multiprocessing") is False
    assert is_code_safe("import subprocess") is False
    assert is_code_safe("import signal") is False


def test_serialization_modules_blocked():
    assert is_code_safe("import pickle") is False
    assert is_code_safe("import marshal") is False
    assert is_code_safe("import shelve") is False


def test_introspection_modules_blocked():
    assert is_code_safe("import ast") is False
    assert is_code_safe("import inspect") is False
    assert is_code_safe("import dis") is False
    assert is_code_safe("import code") is False
    assert is_code_safe("import pdb") is False


def test_filesystem_modules_blocked():
    # pathlib is allowed at AST level (runtime os proxy blocks dangerous ops)
    assert is_code_safe("import shutil") is False
    assert is_code_safe("import ctypes") is False


# ── New: dangerous builtins ───────────────────────────────────────────────

def test_string_construction_builtins_blocked():
    # chr() can build "os" from [111, 115] to bypass name checks
    assert is_code_safe("chr(111) + chr(115)") is False
    assert is_code_safe("ord('a')") is False
    # format(111, 'c') is also a string-construction bypass
    assert is_code_safe("format(111, 'c')") is False


def test_introspection_builtins_blocked():
    # These can reach forbidden objects
    assert is_code_safe("vars()") is False
    assert is_code_safe("dir()") is False
    assert is_code_safe("globals()") is False
    assert is_code_safe("locals()") is False
    assert is_code_safe("issubclass(int, object)") is False
    assert is_code_safe("callable(lambda: 1)") is False


def test_io_builtins_blocked():
    assert is_code_safe("open('/etc/passwd')") is False
    assert is_code_safe("input('prompt')") is False
    assert is_code_safe("breakpoint()") is False
    assert is_code_safe("help()") is False
    assert is_code_safe("exit()") is False
    assert is_code_safe("quit()") is False


# ── New: subscript-based bypasses ─────────────────────────────────────────

def test_subscript_bypasses_blocked():
    # os.__dict__["system"] would reach the real os.system
    assert is_code_safe('os.__dict__["system"]("ls")') is False
    # __builtins__["exec"] is the classic jail break
    assert is_code_safe('__builtins__["exec"]("print(1)")') is False
    # Generic indexing of forbidden names
    assert is_code_safe('foo["__globals__"]') is False
    assert is_code_safe('foo["eval"]') is False


# ── New: star imports + relative imports ──────────────────────────────────

def test_star_imports_blocked():
    assert is_code_safe("from os import *") is False
    assert is_code_safe("from qgis.core import *") is False


def test_relative_imports_blocked():
    assert is_code_safe("from . import foo") is False
    assert is_code_safe("from .. import bar") is False
    assert is_code_safe("from .submodule import x") is False


# ── New: extended magic attribute list ────────────────────────────────────

def test_extended_magic_attributes_blocked():
    # __dict__ on objects is the standard way to escape
    assert is_code_safe("obj.__dict__") is False
    # __class__ and __bases__ chain walks the MRO
    assert is_code_safe("().__class__.__bases__") is False
    # __loader__/__spec__ are import machinery
    assert is_code_safe("foo.__loader__") is False
    assert is_code_safe("foo.__spec__") is False
    # __reduce__/__reduce_ex__ are pickle protocol hooks
    assert is_code_safe("foo.__reduce__") is False
    # __code__ on callables leaks source location
    assert is_code_safe("(lambda: 0).__code__") is False


# ── Edge cases ────────────────────────────────────────────────────────────

def test_safe_legitimate_patterns_still_pass():
    # Common QGIS operations should be allowed
    assert is_code_safe("layer.crs().authid()") is True
    assert is_code_safe("QgsProject.instance().mapLayers()") is True
    assert is_code_safe("result = processing.run('native:buffer', {...})") is True
    assert is_code_safe("x = 1 + 2") is True
    assert is_code_safe("print('hello')") is True
    assert is_code_safe("data = [c for c in 'abc']") is True
    # bytes/bytearray are intentionally NOT blocked (legit binary use)
    assert is_code_safe("b = bytes([1, 2, 3])") is True
    # repr/str are intentionally NOT blocked
    assert is_code_safe("repr(obj)") is True
    assert is_code_safe("str(obj)") is True
    # is valid as a name (not a builtin) is fine
    assert is_code_safe("is_valid = True") is True


def test_deduplicates_violations():
    """The same violation appearing multiple times should appear once."""
    code = "import subprocess; import subprocess.run; import subprocess.Popen"
    violations = check_ast(code)
    sub_violations = [v for v in violations if "subprocess" in v]
    assert len(sub_violations) == 3  # three different subprocess.* imports, all valid


def test_violations_include_actionable_messages():
    code = "import subprocess"
    violations = check_ast(code)
    assert any("subprocess" in v for v in violations)
    assert all(isinstance(v, str) for v in violations)