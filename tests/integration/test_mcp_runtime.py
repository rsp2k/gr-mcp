"""Integration tests for MCP runtime tools via FastMCP Client.

These tests verify the runtime MCP tools work correctly end-to-end,
using a subprocess-based XML-RPC server (no Docker required).

Run with: pytest tests/integration/test_mcp_runtime.py -v
"""

from __future__ import annotations

import socket
import subprocess
import sys
import textwrap
import time
from contextlib import closing
from pathlib import Path
from typing import Any, Generator

import pytest
from fastmcp import Client, FastMCP

from gnuradio_mcp.middlewares.xmlrpc import XmlRpcMiddleware
from gnuradio_mcp.providers.mcp_runtime import McpRuntimeProvider
from gnuradio_mcp.providers.runtime import RuntimeProvider


def extract_raw_value(result) -> Any:
    """Extract raw value from FastMCP result.

    When a tool returns a non-Pydantic value (like int, float, bool),
    FastMCP serializes it as TextContent. This helper parses it back.
    """
    if result.data is not None:
        return result.data
    if result.content and len(result.content) > 0:
        text = result.content[0].text
        # Try to parse as float first (handles scientific notation)
        try:
            return float(text)
        except ValueError:
            pass
        # Try int
        try:
            return int(text)
        except ValueError:
            pass
        # Return as string
        return text
    return None


def find_free_port() -> int:
    """Find an available port on localhost."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("", 0))
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return s.getsockname()[1]


@pytest.fixture
def xmlrpc_server_script(tmp_path: Path) -> Path:
    """Create a test XML-RPC server script that mimics GNU Radio."""
    script = tmp_path / "test_xmlrpc_server.py"
    script.write_text(
        textwrap.dedent(
            '''\
            #!/usr/bin/env python3
            """Test XML-RPC server mimicking GNU Radio flowgraph interface."""

            import os
            from xmlrpc.server import SimpleXMLRPCServer

            PORT = int(os.environ.get("XMLRPC_PORT", 8080))

            _variables = {
                "frequency": 101.1e6,
                "amplitude": 0.5,
                "gain": 10,
            }

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

            def start():
                pass

            def stop():
                pass

            def lock():
                pass

            def unlock():
                pass

            def main():
                server = SimpleXMLRPCServer(("0.0.0.0", PORT), allow_none=True)
                server.register_introspection_functions()
                server.register_function(get_frequency)
                server.register_function(set_frequency)
                server.register_function(get_amplitude)
                server.register_function(set_amplitude)
                server.register_function(get_gain)
                server.register_function(set_gain)
                server.register_function(start)
                server.register_function(stop)
                server.register_function(lock)
                server.register_function(unlock)
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
    """Start the XML-RPC server subprocess."""
    port = find_free_port()
    env = {**dict(__import__("os").environ), "XMLRPC_PORT": str(port)}

    proc = subprocess.Popen(
        [sys.executable, str(xmlrpc_server_script)],
        env=env,
        stderr=subprocess.PIPE,
        stdout=subprocess.PIPE,
    )

    # Wait for server to be ready
    deadline = time.time() + 10
    while time.time() < deadline:
        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            raise RuntimeError(f"Server exited: {stderr.decode()} {stdout.decode()}")
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

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()


@pytest.fixture
def runtime_mcp_app() -> FastMCP:
    """Create FastMCP app with runtime tools (no Docker)."""
    app = FastMCP("gr-mcp-runtime-test")
    # RuntimeProvider without Docker — XML-RPC tools still available
    provider = RuntimeProvider(docker_mw=None)
    McpRuntimeProvider(app, provider)
    return app


@pytest.fixture
async def runtime_client(runtime_mcp_app: FastMCP):
    """Create FastMCP client for runtime tools.

    Automatically enables runtime mode so runtime tools are available.
    """
    async with Client(runtime_mcp_app) as client:
        # Enable runtime mode to register runtime tools dynamically
        await client.call_tool(name="enable_runtime_mode")
        yield client


class TestRuntimeMcpToolsNoConnection:
    """Test runtime tools before connecting to a server."""

    async def test_get_status_not_connected(self, runtime_client: Client):
        """get_status should work without connection, showing disconnected state."""
        result = await runtime_client.call_tool(name="get_status")

        assert result.data is not None
        assert result.data.connected is False
        assert result.data.connection is None

    async def test_list_variables_requires_connection(self, runtime_client: Client):
        """list_variables should raise when not connected."""
        with pytest.raises(Exception, match="Not connected"):
            await runtime_client.call_tool(name="list_variables")

    async def test_get_variable_requires_connection(self, runtime_client: Client):
        """get_variable should raise when not connected."""
        with pytest.raises(Exception, match="Not connected"):
            await runtime_client.call_tool(
                name="get_variable", arguments={"name": "frequency"}
            )

    async def test_disconnect_idempotent(self, runtime_client: Client):
        """disconnect should succeed even when not connected."""
        result = await runtime_client.call_tool(name="disconnect")
        assert result.data is True


class TestRuntimeMcpToolsConnected:
    """Test runtime tools connected to XML-RPC server."""

    async def test_connect_success(
        self, runtime_client: Client, xmlrpc_server: tuple[subprocess.Popen, int]
    ):
        """Test connecting to XML-RPC server via MCP tool."""
        _, port = xmlrpc_server
        url = f"http://127.0.0.1:{port}"

        result = await runtime_client.call_tool(name="connect", arguments={"url": url})

        assert result.data is not None
        assert result.data.url == url
        assert "get_frequency" in result.data.methods

    async def test_connect_updates_status(
        self, runtime_client: Client, xmlrpc_server: tuple[subprocess.Popen, int]
    ):
        """After connecting, status should show connected."""
        _, port = xmlrpc_server
        url = f"http://127.0.0.1:{port}"

        await runtime_client.call_tool(name="connect", arguments={"url": url})
        result = await runtime_client.call_tool(name="get_status")

        assert result.data.connected is True
        assert result.data.connection.url == url

    async def test_list_variables(
        self, runtime_client: Client, xmlrpc_server: tuple[subprocess.Popen, int]
    ):
        """Test listing variables after connecting."""
        _, port = xmlrpc_server
        url = f"http://127.0.0.1:{port}"

        await runtime_client.call_tool(name="connect", arguments={"url": url})
        result = await runtime_client.call_tool(name="list_variables")

        assert result.data is not None
        names = {v.name for v in result.data}
        assert "frequency" in names
        assert "amplitude" in names
        assert "gain" in names

    async def test_get_variable(
        self, runtime_client: Client, xmlrpc_server: tuple[subprocess.Popen, int]
    ):
        """Test getting a variable value."""
        _, port = xmlrpc_server
        url = f"http://127.0.0.1:{port}"

        await runtime_client.call_tool(name="connect", arguments={"url": url})
        result = await runtime_client.call_tool(
            name="get_variable", arguments={"name": "frequency"}
        )

        # get_variable returns raw values (float), not Pydantic models
        assert extract_raw_value(result) == 101.1e6

    async def test_set_variable(
        self, runtime_client: Client, xmlrpc_server: tuple[subprocess.Popen, int]
    ):
        """Test setting a variable value."""
        _, port = xmlrpc_server
        url = f"http://127.0.0.1:{port}"

        await runtime_client.call_tool(name="connect", arguments={"url": url})

        # Set new value
        set_result = await runtime_client.call_tool(
            name="set_variable", arguments={"name": "frequency", "value": 107.2e6}
        )
        assert set_result.data is True

        # Verify it was set
        get_result = await runtime_client.call_tool(
            name="get_variable", arguments={"name": "frequency"}
        )
        assert extract_raw_value(get_result) == 107.2e6

    async def test_flowgraph_control_start(
        self, runtime_client: Client, xmlrpc_server: tuple[subprocess.Popen, int]
    ):
        """Test starting the flowgraph."""
        _, port = xmlrpc_server
        url = f"http://127.0.0.1:{port}"

        await runtime_client.call_tool(name="connect", arguments={"url": url})
        result = await runtime_client.call_tool(name="start")

        assert result.data is True

    async def test_flowgraph_control_stop(
        self, runtime_client: Client, xmlrpc_server: tuple[subprocess.Popen, int]
    ):
        """Test stopping the flowgraph."""
        _, port = xmlrpc_server
        url = f"http://127.0.0.1:{port}"

        await runtime_client.call_tool(name="connect", arguments={"url": url})
        result = await runtime_client.call_tool(name="stop")

        assert result.data is True

    async def test_flowgraph_control_lock_unlock(
        self, runtime_client: Client, xmlrpc_server: tuple[subprocess.Popen, int]
    ):
        """Test lock/unlock sequence."""
        _, port = xmlrpc_server
        url = f"http://127.0.0.1:{port}"

        await runtime_client.call_tool(name="connect", arguments={"url": url})

        lock_result = await runtime_client.call_tool(name="lock")
        assert lock_result.data is True

        unlock_result = await runtime_client.call_tool(name="unlock")
        assert unlock_result.data is True

    async def test_disconnect_clears_connection(
        self, runtime_client: Client, xmlrpc_server: tuple[subprocess.Popen, int]
    ):
        """Test disconnecting clears the connection state."""
        _, port = xmlrpc_server
        url = f"http://127.0.0.1:{port}"

        await runtime_client.call_tool(name="connect", arguments={"url": url})
        await runtime_client.call_tool(name="disconnect")

        # Status should show disconnected
        result = await runtime_client.call_tool(name="get_status")
        assert result.data.connected is False


class TestRuntimeMcpToolsFullWorkflow:
    """End-to-end workflow tests."""

    async def test_tuning_workflow(
        self, runtime_client: Client, xmlrpc_server: tuple[subprocess.Popen, int]
    ):
        """Test a complete tuning workflow: connect, read, tune, verify."""
        _, port = xmlrpc_server
        url = f"http://127.0.0.1:{port}"

        # Connect
        await runtime_client.call_tool(name="connect", arguments={"url": url})

        # Read initial frequency
        initial = await runtime_client.call_tool(
            name="get_variable", arguments={"name": "frequency"}
        )
        assert extract_raw_value(initial) == 101.1e6

        # Tune to new frequency with lock/unlock
        await runtime_client.call_tool(name="lock")
        await runtime_client.call_tool(
            name="set_variable", arguments={"name": "frequency", "value": 98.5e6}
        )
        await runtime_client.call_tool(name="unlock")

        # Verify
        final = await runtime_client.call_tool(
            name="get_variable", arguments={"name": "frequency"}
        )
        assert extract_raw_value(final) == 98.5e6

        # Disconnect
        await runtime_client.call_tool(name="disconnect")

    async def test_scan_and_tune_workflow(
        self, runtime_client: Client, xmlrpc_server: tuple[subprocess.Popen, int]
    ):
        """Simulate scanning through frequencies (mimics FM scanner use case)."""
        _, port = xmlrpc_server
        url = f"http://127.0.0.1:{port}"

        await runtime_client.call_tool(name="connect", arguments={"url": url})

        # Scan through several frequencies
        test_frequencies = [88.1e6, 91.5e6, 95.7e6, 101.1e6, 107.9e6]

        for freq in test_frequencies:
            await runtime_client.call_tool(
                name="set_variable", arguments={"name": "frequency", "value": freq}
            )
            result = await runtime_client.call_tool(
                name="get_variable", arguments={"name": "frequency"}
            )
            assert extract_raw_value(result) == freq

        await runtime_client.call_tool(name="disconnect")


class TestDynamicRuntimeMode:
    """Test dynamic tool registration via runtime mode toggle."""

    async def test_runtime_mode_starts_disabled(self, runtime_mcp_app: FastMCP):
        """Runtime mode should be disabled by default."""
        async with Client(runtime_mcp_app) as client:
            result = await client.call_tool(name="get_runtime_mode")
            assert result.data.enabled is False
            assert result.data.tools_registered == []

    async def test_enable_runtime_mode_registers_tools(self, runtime_mcp_app: FastMCP):
        """Enabling runtime mode should register runtime tools."""
        async with Client(runtime_mcp_app) as client:
            # Check tools before enabling
            tools_before = await client.list_tools()

            result = await client.call_tool(name="enable_runtime_mode")

            assert result.data.enabled is True
            assert len(result.data.tools_registered) > 0
            assert "connect" in result.data.tools_registered
            assert "list_variables" in result.data.tools_registered

            # Verify tools are actually callable
            tools_after = await client.list_tools()
            assert len(tools_after) > len(tools_before)

    async def test_disable_runtime_mode_removes_tools(self, runtime_mcp_app: FastMCP):
        """Disabling runtime mode should remove runtime tools."""
        async with Client(runtime_mcp_app) as client:
            # Enable first
            await client.call_tool(name="enable_runtime_mode")
            tools_enabled = await client.list_tools()

            # Now disable
            result = await client.call_tool(name="disable_runtime_mode")

            assert result.data.enabled is False
            assert result.data.tools_registered == []

            # Verify tools are actually removed
            tools_disabled = await client.list_tools()
            assert len(tools_disabled) < len(tools_enabled)

    async def test_enable_runtime_mode_idempotent(self, runtime_mcp_app: FastMCP):
        """Enabling runtime mode twice should be safe."""
        async with Client(runtime_mcp_app) as client:
            result1 = await client.call_tool(name="enable_runtime_mode")
            result2 = await client.call_tool(name="enable_runtime_mode")

            assert result1.data.enabled is True
            assert result2.data.enabled is True
            assert result1.data.tools_registered == result2.data.tools_registered

    async def test_disable_runtime_mode_idempotent(self, runtime_mcp_app: FastMCP):
        """Disabling runtime mode twice should be safe."""
        async with Client(runtime_mcp_app) as client:
            result1 = await client.call_tool(name="disable_runtime_mode")
            result2 = await client.call_tool(name="disable_runtime_mode")

            assert result1.data.enabled is False
            assert result2.data.enabled is False


class TestClientCapabilities:
    """Test MCP client capability inspection tools."""

    async def test_get_client_capabilities_returns_structured_data(
        self, runtime_mcp_app: FastMCP
    ):
        """get_client_capabilities should return structured capability info."""
        async with Client(runtime_mcp_app) as client:
            result = await client.call_tool(name="get_client_capabilities")

            # Should have structured capability objects
            assert result.data is not None
            assert hasattr(result.data, "roots")
            assert hasattr(result.data, "sampling")
            assert hasattr(result.data, "elicitation")

            # Each capability should have 'supported' field
            assert hasattr(result.data.roots, "supported")
            assert hasattr(result.data.sampling, "supported")
            assert hasattr(result.data.elicitation, "supported")

    async def test_list_client_roots_returns_list(self, runtime_mcp_app: FastMCP):
        """list_client_roots should return a list (may be empty in test)."""
        async with Client(runtime_mcp_app) as client:
            result = await client.call_tool(name="list_client_roots")

            # Should return a list (FastMCP test client may not advertise roots)
            assert isinstance(result.data, list) or result.data is None
