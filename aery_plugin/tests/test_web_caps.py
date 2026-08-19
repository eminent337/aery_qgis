"""Tests for the web_caps byte-cap helper (ported from GeoLibre's readTextCapped)."""

import io

import pytest

from aery_plugin.web_caps import (
    DEFAULT_MAX_BYTES,
    ResponseTooLargeError,
    read_capped,
    read_capped_text,
)


class _FakeResp:
    """Minimal urllib-response stand-in with a chunked read()."""

    def __init__(self, data: bytes):
        self._data = data

    def read(self, n: int = -1) -> bytes:
        if self._data:
            chunk = self._data[:n] if n > 0 else self._data
            self._data = self._data[len(chunk):]
            return chunk
        return b""


def test_read_capped_small_body():
    data = b"hello world"
    assert read_capped(_FakeResp(data)) == data


def test_read_capped_text_decodes():
    data = "héllo wörld".encode("utf-8")
    assert read_capped_text(_FakeResp(data)) == "héllo wörld"


def test_read_capped_large_body_raises():
    big = b"x" * (DEFAULT_MAX_BYTES + 1)
    with pytest.raises(ResponseTooLargeError):
        read_capped(_FakeResp(big))


def test_read_capped_exact_limit_ok():
    data = b"y" * DEFAULT_MAX_BYTES
    assert len(read_capped(_FakeResp(data))) == DEFAULT_MAX_BYTES


def test_read_capped_custom_limit():
    data = b"z" * 2048
    with pytest.raises(ResponseTooLargeError):
        read_capped(_FakeResp(data), max_bytes=1024)
