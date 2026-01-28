"""Unit tests for port utilities."""

import socket

import pytest

from gnuradio_mcp.middlewares.ports import (
    PortConflictError,
    detect_xmlrpc_port,
    find_free_port,
    is_port_available,
    patch_xmlrpc_port,
)

# Sample flowgraph snippet matching what GRC actually generates
SAMPLE_FLOWGRAPH = """\
#!/usr/bin/env python3
import sys
from xmlrpc.server import SimpleXMLRPCServer

class top_block:
    def __init__(self):
        self.xmlrpc_server_0 = SimpleXMLRPCServer(('localhost', 8080), allow_none=True)
        self.xmlrpc_server_0.register_instance(self)

if __name__ == '__main__':
    tb = top_block()
    tb.xmlrpc_server_0.serve_forever()
"""

SAMPLE_NO_XMLRPC = """\
#!/usr/bin/env python3
class top_block:
    def __init__(self):
        self.source = some_source()
"""


class TestIsPortAvailable:
    def test_free_port_is_available(self):
        # Get a port the OS says is free, then check our function agrees
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            port = s.getsockname()[1]
        # Socket is closed, port should be free
        assert is_port_available(port) is True

    def test_occupied_port_is_unavailable(self):
        # Hold a port open and verify our function detects it
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            s.listen(1)
            # Socket still bound and listening — port is occupied
            assert is_port_available(port) is False


class TestFindFreePort:
    def test_returns_available_port(self):
        port = find_free_port()
        assert isinstance(port, int)
        assert 1024 <= port <= 65535

    def test_returned_port_is_usable(self):
        port = find_free_port()
        # Should be able to bind to it
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("127.0.0.1", port))


class TestDetectXmlrpcPort:
    def test_detects_port(self, tmp_path):
        fg = tmp_path / "flowgraph.py"
        fg.write_text(SAMPLE_FLOWGRAPH)
        assert detect_xmlrpc_port(fg) == 8080

    def test_returns_none_when_missing(self, tmp_path):
        fg = tmp_path / "no_xmlrpc.py"
        fg.write_text(SAMPLE_NO_XMLRPC)
        assert detect_xmlrpc_port(fg) is None

    def test_detects_different_port(self, tmp_path):
        fg = tmp_path / "custom.py"
        fg.write_text(SAMPLE_FLOWGRAPH.replace("8080", "9999"))
        assert detect_xmlrpc_port(fg) == 9999


class TestPatchXmlrpcPort:
    def test_patches_port(self, tmp_path):
        fg = tmp_path / "flowgraph.py"
        fg.write_text(SAMPLE_FLOWGRAPH)

        patched = patch_xmlrpc_port(fg, 12345)
        content = patched.read_text()
        assert "12345" in content
        assert "8080" not in content

    def test_preserves_original(self, tmp_path):
        fg = tmp_path / "flowgraph.py"
        fg.write_text(SAMPLE_FLOWGRAPH)
        original_text = fg.read_text()

        patch_xmlrpc_port(fg, 12345)
        assert fg.read_text() == original_text

    def test_patched_file_in_same_directory(self, tmp_path):
        fg = tmp_path / "flowgraph.py"
        fg.write_text(SAMPLE_FLOWGRAPH)

        patched = patch_xmlrpc_port(fg, 12345)
        assert patched.parent == fg.parent

    def test_raises_on_no_match(self, tmp_path):
        fg = tmp_path / "no_xmlrpc.py"
        fg.write_text(SAMPLE_NO_XMLRPC)

        with pytest.raises(ValueError, match="No SimpleXMLRPCServer"):
            patch_xmlrpc_port(fg, 12345)


class TestPortConflictError:
    def test_is_runtime_error(self):
        err = PortConflictError("port 8080 in use")
        assert isinstance(err, RuntimeError)
