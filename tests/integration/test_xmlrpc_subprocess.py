"""Integration tests for XmlRpcMiddleware using a real subprocess.

These tests spawn an actual XML-RPC server without requiring Docker,
making them faster and more reliable for CI/CD pipelines.

Run with: pytest tests/integration/test_xmlrpc_subprocess.py -v
"""

from __future__ import annotations

import socket
import subprocess
import sys
import textwrap
import time
from contextlib import closing
from pathlib import Path
from typing import Generator

import pytest

from gnuradio_mcp.middlewares.xmlrpc import XmlRpcMiddleware
from gnuradio_mcp.models import ConnectionInfoModel, VariableModel


def find_free_port() -> int:
    """Find an available port on localhost."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


@pytest.fixture
def xmlrpc_server_script(tmp_path: Path) -> Path:
    """Create a test XML-RPC server script that mimics GNU Radio.

    This server simulates the XML-RPC interface exposed by GNU Radio
    flowgraphs, including:
    - get_*/set_* variable accessors
    - start/stop/lock/unlock flowgraph control
    - system.listMethods introspection (optional)
    """
    script = tmp_path / "test_xmlrpc_server.py"
    script.write_text(
        textwrap.dedent(
            '''\
            #!/usr/bin/env python3
            """Test XML-RPC server mimicking GNU Radio flowgraph interface."""

            import os
            import sys
            from xmlrpc.server import SimpleXMLRPCServer

            PORT = int(os.environ.get("XMLRPC_PORT", 8080))
            ENABLE_INTROSPECTION = os.environ.get("ENABLE_INTROSPECTION", "1") == "1"

            # Simulated flowgraph variables with various types
            _variables = {
                "frequency": 101.1e6,    # float (Hz)
                "amplitude": 0.5,        # float (0-1)
                "gain": 10,              # int (dB)
                "enabled": True,         # bool
            }

            _flowgraph_state = {
                "running": False,
                "locked": False,
            }


            # Variable accessors (GNU Radio pattern: get_<var> / set_<var>)
            def get_frequency():
                return _variables["frequency"]

            def set_frequency(val):
                _variables["frequency"] = float(val)

            def get_amplitude():
                return _variables["amplitude"]

            def set_amplitude(val):
                _variables["amplitude"] = float(val)

            def get_gain():
                return _variables["gain"]

            def set_gain(val):
                _variables["gain"] = int(val)

            def get_enabled():
                return _variables["enabled"]

            def set_enabled(val):
                _variables["enabled"] = bool(val)


            # Read-only variable (no setter)
            def get_sample_rate():
                return 2.4e6


            # Flowgraph control
            def start():
                _flowgraph_state["running"] = True
                print("Flowgraph started", file=sys.stderr, flush=True)

            def stop():
                _flowgraph_state["running"] = False
                print("Flowgraph stopped", file=sys.stderr, flush=True)

            def lock():
                _flowgraph_state["locked"] = True
                print("Flowgraph locked", file=sys.stderr, flush=True)

            def unlock():
                _flowgraph_state["locked"] = False
                print("Flowgraph unlocked", file=sys.stderr, flush=True)


            def main():
                server = SimpleXMLRPCServer(("0.0.0.0", PORT), allow_none=True)

                if ENABLE_INTROSPECTION:
                    server.register_introspection_functions()

                # Register all functions
                server.register_function(get_frequency)
                server.register_function(set_frequency)
                server.register_function(get_amplitude)
                server.register_function(set_amplitude)
                server.register_function(get_gain)
                server.register_function(set_gain)
                server.register_function(get_enabled)
                server.register_function(set_enabled)
                server.register_function(get_sample_rate)
                server.register_function(start)
                server.register_function(stop)
                server.register_function(lock)
                server.register_function(unlock)

                print(f"XML-RPC server ready on port {PORT}", file=sys.stderr, flush=True)
                server.serve_forever()


            if __name__ == "__main__":
                main()
            '''
        )
    )
    return script


@pytest.fixture
def xmlrpc_server(
    xmlrpc_server_script: Path,
) -> Generator[tuple[subprocess.Popen, int], None, None]:
    """Start the XML-RPC server subprocess and wait for it to be ready."""
    port = find_free_port()
    env = {**dict(__import__("os").environ), "XMLRPC_PORT": str(port)}

    proc = subprocess.Popen(
        [sys.executable, str(xmlrpc_server_script)],
        env=env,
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )

    # Wait for server to be ready (reads "ready" from stderr)
    deadline = time.time() + 10
    while time.time() < deadline:
        if proc.poll() is not None:
            # Process exited unexpectedly
            stdout, stderr = proc.communicate()
            raise RuntimeError(
                f"XML-RPC server exited: {stderr.decode()} {stdout.decode()}"
            )

        # Try connecting
        try:
            sock = socket.create_connection(("127.0.0.1", port), timeout=0.5)
            sock.close()
            break
        except (ConnectionRefusedError, OSError):
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("XML-RPC server did not start in time")

    yield proc, port

    # Cleanup
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


class TestXmlRpcMiddlewareIntegration:
    """Integration tests for XmlRpcMiddleware against a real server."""

    def test_connect_success(self, xmlrpc_server: tuple[subprocess.Popen, int]):
        """Test connecting to a real XML-RPC server."""
        _, port = xmlrpc_server
        url = f"http://127.0.0.1:{port}"

        mw = XmlRpcMiddleware.connect(url)

        assert mw is not None
        assert mw._url == url

    def test_connect_failure(self):
        """Test connection failure to non-existent server."""
        # Use a port that's very unlikely to be in use
        url = "http://127.0.0.1:59999"

        with pytest.raises(ConnectionRefusedError):
            XmlRpcMiddleware.connect(url)

    def test_get_connection_info(self, xmlrpc_server: tuple[subprocess.Popen, int]):
        """Test connection info with introspection enabled."""
        _, port = xmlrpc_server
        mw = XmlRpcMiddleware.connect(f"http://127.0.0.1:{port}")

        info = mw.get_connection_info(xmlrpc_port=port)

        assert isinstance(info, ConnectionInfoModel)
        assert info.xmlrpc_port == port
        # Should have our methods (excluding system.*)
        assert "get_frequency" in info.methods
        assert "set_frequency" in info.methods
        assert "start" in info.methods

    def test_list_variables_discovers_all(
        self, xmlrpc_server: tuple[subprocess.Popen, int]
    ):
        """Test variable discovery finds all get_*/set_* pairs."""
        _, port = xmlrpc_server
        mw = XmlRpcMiddleware.connect(f"http://127.0.0.1:{port}")

        variables = mw.list_variables()

        names = {v.name for v in variables}
        # These have both get_ and set_
        assert "frequency" in names
        assert "amplitude" in names
        assert "gain" in names
        assert "enabled" in names
        # sample_rate has only get_, should be excluded
        assert "sample_rate" not in names

    def test_list_variables_retrieves_values(
        self, xmlrpc_server: tuple[subprocess.Popen, int]
    ):
        """Test that list_variables retrieves actual values."""
        _, port = xmlrpc_server
        mw = XmlRpcMiddleware.connect(f"http://127.0.0.1:{port}")

        variables = mw.list_variables()
        var_dict = {v.name: v.value for v in variables}

        assert var_dict["frequency"] == 101.1e6
        assert var_dict["amplitude"] == 0.5
        assert var_dict["gain"] == 10
        assert var_dict["enabled"] is True

    def test_get_variable_float(self, xmlrpc_server: tuple[subprocess.Popen, int]):
        """Test reading a float variable."""
        _, port = xmlrpc_server
        mw = XmlRpcMiddleware.connect(f"http://127.0.0.1:{port}")

        value = mw.get_variable("frequency")

        assert value == 101.1e6
        assert isinstance(value, float)

    def test_get_variable_int(self, xmlrpc_server: tuple[subprocess.Popen, int]):
        """Test reading an integer variable."""
        _, port = xmlrpc_server
        mw = XmlRpcMiddleware.connect(f"http://127.0.0.1:{port}")

        value = mw.get_variable("gain")

        assert value == 10
        assert isinstance(value, int)

    def test_get_variable_bool(self, xmlrpc_server: tuple[subprocess.Popen, int]):
        """Test reading a boolean variable."""
        _, port = xmlrpc_server
        mw = XmlRpcMiddleware.connect(f"http://127.0.0.1:{port}")

        value = mw.get_variable("enabled")

        assert value is True
        assert isinstance(value, bool)

    def test_set_variable_float(self, xmlrpc_server: tuple[subprocess.Popen, int]):
        """Test setting a float variable and reading it back."""
        _, port = xmlrpc_server
        mw = XmlRpcMiddleware.connect(f"http://127.0.0.1:{port}")

        # Set new value
        result = mw.set_variable("frequency", 107.2e6)
        assert result is True

        # Verify it was set
        value = mw.get_variable("frequency")
        assert value == 107.2e6

    def test_set_variable_int(self, xmlrpc_server: tuple[subprocess.Popen, int]):
        """Test setting an integer variable."""
        _, port = xmlrpc_server
        mw = XmlRpcMiddleware.connect(f"http://127.0.0.1:{port}")

        mw.set_variable("gain", 20)
        value = mw.get_variable("gain")

        assert value == 20

    def test_set_variable_bool(self, xmlrpc_server: tuple[subprocess.Popen, int]):
        """Test setting a boolean variable."""
        _, port = xmlrpc_server
        mw = XmlRpcMiddleware.connect(f"http://127.0.0.1:{port}")

        mw.set_variable("enabled", False)
        value = mw.get_variable("enabled")

        assert value is False


class TestFlowgraphControlIntegration:
    """Integration tests for flowgraph control commands."""

    def test_start(self, xmlrpc_server: tuple[subprocess.Popen, int]):
        """Test starting the flowgraph."""
        _, port = xmlrpc_server
        mw = XmlRpcMiddleware.connect(f"http://127.0.0.1:{port}")

        result = mw.start()

        assert result is True

    def test_stop(self, xmlrpc_server: tuple[subprocess.Popen, int]):
        """Test stopping the flowgraph."""
        _, port = xmlrpc_server
        mw = XmlRpcMiddleware.connect(f"http://127.0.0.1:{port}")

        result = mw.stop()

        assert result is True

    def test_lock(self, xmlrpc_server: tuple[subprocess.Popen, int]):
        """Test locking the flowgraph."""
        _, port = xmlrpc_server
        mw = XmlRpcMiddleware.connect(f"http://127.0.0.1:{port}")

        result = mw.lock()

        assert result is True

    def test_unlock(self, xmlrpc_server: tuple[subprocess.Popen, int]):
        """Test unlocking the flowgraph."""
        _, port = xmlrpc_server
        mw = XmlRpcMiddleware.connect(f"http://127.0.0.1:{port}")

        result = mw.unlock()

        assert result is True

    def test_lock_unlock_sequence(self, xmlrpc_server: tuple[subprocess.Popen, int]):
        """Test the lock/unlock sequence used for thread-safe updates."""
        _, port = xmlrpc_server
        mw = XmlRpcMiddleware.connect(f"http://127.0.0.1:{port}")

        # Typical GNU Radio pattern: lock, update, unlock
        assert mw.lock() is True
        mw.set_variable("frequency", 98.5e6)
        assert mw.unlock() is True

        # Verify the update took effect
        assert mw.get_variable("frequency") == 98.5e6


class TestConnectionLifecycle:
    """Tests for connection management."""

    def test_close_clears_proxy(self, xmlrpc_server: tuple[subprocess.Popen, int]):
        """Test that close() clears the proxy reference."""
        _, port = xmlrpc_server
        mw = XmlRpcMiddleware.connect(f"http://127.0.0.1:{port}")

        mw.close()

        assert mw._proxy is None

    def test_reconnect_after_close(self, xmlrpc_server: tuple[subprocess.Popen, int]):
        """Test reconnecting after closing."""
        _, port = xmlrpc_server
        url = f"http://127.0.0.1:{port}"

        # First connection
        mw1 = XmlRpcMiddleware.connect(url)
        mw1.set_variable("gain", 15)
        mw1.close()

        # Reconnect
        mw2 = XmlRpcMiddleware.connect(url)
        value = mw2.get_variable("gain")

        # Value should persist (server is still running)
        assert value == 15
