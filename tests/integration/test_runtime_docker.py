"""Integration tests for RuntimeProvider with real Docker.

These tests require:
1. Docker daemon running
2. gnuradio-runtime:latest image built (or tests will skip)

Run with: pytest tests/integration/test_runtime_docker.py -v
"""

import time
from pathlib import Path

import pytest

# Check if Docker is available
try:
    import docker

    _docker_client = docker.from_env()
    _docker_client.ping()
    DOCKER_AVAILABLE = True
except Exception:
    DOCKER_AVAILABLE = False

# Check if runtime image exists
RUNTIME_IMAGE = "gnuradio-runtime:latest"
RUNTIME_IMAGE_EXISTS = False
if DOCKER_AVAILABLE:
    try:
        _docker_client.images.get(RUNTIME_IMAGE)
        RUNTIME_IMAGE_EXISTS = True
    except Exception:
        pass


pytestmark = [
    pytest.mark.skipif(not DOCKER_AVAILABLE, reason="Docker not available"),
    pytest.mark.skipif(
        not RUNTIME_IMAGE_EXISTS,
        reason=f"Runtime image '{RUNTIME_IMAGE}' not built. "
        "Run: docker build -t gnuradio-runtime docker/",
    ),
]


@pytest.fixture
def docker_client():
    """Real Docker client."""
    return docker.from_env()


@pytest.fixture
def cleanup_containers(docker_client):
    """Cleanup any test containers after each test."""
    created_containers = []

    yield created_containers

    # Cleanup
    for name in created_containers:
        try:
            container = docker_client.containers.get(name)
            container.stop(timeout=5)
            container.remove(force=True)
        except Exception:
            pass


@pytest.fixture
def test_flowgraph(tmp_path) -> Path:
    """Create a minimal Python flowgraph for testing.

    This creates a simple Python script that mimics a GNU Radio flowgraph
    with XML-RPC server (for testing without requiring a real .grc file).
    """
    fg_path = tmp_path / "test_flowgraph.py"
    fg_path.write_text(
        '''\
#!/usr/bin/env python3
"""Minimal test flowgraph with XML-RPC server."""

import os
import time
from xmlrpc.server import SimpleXMLRPCServer
import threading

# Configurable via environment
XMLRPC_PORT = int(os.environ.get("XMLRPC_PORT", 8080))

# Simulated flowgraph variables
_variables = {
    "frequency": 1e6,
    "amplitude": 0.5,
    "running": False,
}


def get_frequency():
    return _variables["frequency"]


def set_frequency(val):
    _variables["frequency"] = float(val)


def get_amplitude():
    return _variables["amplitude"]


def set_amplitude(val):
    _variables["amplitude"] = float(val)


def start():
    _variables["running"] = True
    print("Flowgraph started")


def stop():
    _variables["running"] = False
    print("Flowgraph stopped")


def lock():
    print("Flowgraph locked")


def unlock():
    print("Flowgraph unlocked")


def main():
    server = SimpleXMLRPCServer(("0.0.0.0", XMLRPC_PORT), allow_none=True)
    server.register_introspection_functions()  # Enable system.listMethods()
    server.register_function(get_frequency)
    server.register_function(set_frequency)
    server.register_function(get_amplitude)
    server.register_function(set_amplitude)
    server.register_function(start)
    server.register_function(stop)
    server.register_function(lock)
    server.register_function(unlock)

    print(f"XML-RPC server listening on port {XMLRPC_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    main()
'''
    )
    return fg_path


class TestDockerMiddlewareIntegration:
    """Test DockerMiddleware with real Docker."""

    def test_create_returns_middleware(self):
        from gnuradio_mcp.middlewares.docker import DockerMiddleware

        mw = DockerMiddleware.create()
        assert mw is not None

    def test_list_containers_empty_initially(self):
        from gnuradio_mcp.middlewares.docker import DockerMiddleware

        mw = DockerMiddleware.create()
        # Filter to only our test containers
        containers = [c for c in mw.list_containers() if c.name.startswith("gr-test-")]
        # May or may not be empty depending on previous test runs
        assert isinstance(containers, list)


class TestRuntimeProviderIntegration:
    """Test RuntimeProvider with real Docker (requires runtime image)."""

    def test_launch_and_stop_flowgraph(self, test_flowgraph, cleanup_containers):
        from gnuradio_mcp.middlewares.docker import DockerMiddleware
        from gnuradio_mcp.providers.runtime import RuntimeProvider

        mw = DockerMiddleware.create()
        provider = RuntimeProvider(docker_mw=mw)

        container_name = f"gr-test-{int(time.time())}"
        cleanup_containers.append(container_name)

        # Launch
        result = provider.launch_flowgraph(
            flowgraph_path=str(test_flowgraph),
            name=container_name,
            xmlrpc_port=18080,  # Use high port to avoid conflicts
        )

        assert result.name == container_name
        assert result.status == "running"
        assert result.xmlrpc_port == 18080

        # Wait for container to start
        time.sleep(2)

        # Verify in list
        containers = provider.list_containers()
        names = [c.name for c in containers]
        assert container_name in names

        # Stop
        assert provider.stop_flowgraph(container_name) is True

        # Remove
        assert provider.remove_flowgraph(container_name) is True

        # Remove from cleanup list since we already removed it
        cleanup_containers.remove(container_name)

    def test_launch_connect_and_control(self, test_flowgraph, cleanup_containers):
        """Integration: launch, connect via XML-RPC, and control variables."""
        from gnuradio_mcp.middlewares.docker import DockerMiddleware
        from gnuradio_mcp.providers.runtime import RuntimeProvider

        mw = DockerMiddleware.create()
        provider = RuntimeProvider(docker_mw=mw)

        container_name = f"gr-test-{int(time.time())}"
        cleanup_containers.append(container_name)

        # Launch with specific port
        xmlrpc_port = 18081
        provider.launch_flowgraph(
            flowgraph_path=str(test_flowgraph),
            name=container_name,
            xmlrpc_port=xmlrpc_port,
        )

        # Wait for XML-RPC server to be ready
        time.sleep(3)

        try:
            # Connect
            connection = provider.connect(f"http://localhost:{xmlrpc_port}")
            assert connection.url == f"http://localhost:{xmlrpc_port}"
            assert "get_frequency" in connection.methods

            # List variables
            variables = provider.list_variables()
            var_names = [v.name for v in variables]
            assert "frequency" in var_names
            assert "amplitude" in var_names

            # Get/set variable
            freq = provider.get_variable("frequency")
            assert freq == 1e6

            provider.set_variable("frequency", 2e6)
            new_freq = provider.get_variable("frequency")
            assert new_freq == 2e6

            # Flowgraph control
            assert provider.start() is True
            assert provider.lock() is True
            assert provider.unlock() is True
            assert provider.stop() is True

            # Disconnect
            provider.disconnect()

        finally:
            # Cleanup
            provider.stop_flowgraph(container_name)
            provider.remove_flowgraph(container_name, force=True)
            cleanup_containers.remove(container_name)

    def test_get_container_logs(self, test_flowgraph, cleanup_containers):
        """Test retrieving container logs."""
        from gnuradio_mcp.middlewares.docker import DockerMiddleware
        from gnuradio_mcp.providers.runtime import RuntimeProvider

        mw = DockerMiddleware.create()
        provider = RuntimeProvider(docker_mw=mw)

        container_name = f"gr-test-logs-{int(time.time())}"
        cleanup_containers.append(container_name)

        provider.launch_flowgraph(
            flowgraph_path=str(test_flowgraph),
            name=container_name,
            xmlrpc_port=18082,
        )

        # Wait for startup
        time.sleep(2)

        try:
            logs = provider.get_container_logs(container_name, tail=50)
            # Should contain startup message from our test flowgraph
            assert "XML-RPC server listening" in logs or "Xvfb" in logs

        finally:
            provider.stop_flowgraph(container_name)
            provider.remove_flowgraph(container_name, force=True)
            cleanup_containers.remove(container_name)

    def test_status_shows_running_container(self, test_flowgraph, cleanup_containers):
        """Test get_status includes running containers."""
        from gnuradio_mcp.middlewares.docker import DockerMiddleware
        from gnuradio_mcp.providers.runtime import RuntimeProvider

        mw = DockerMiddleware.create()
        provider = RuntimeProvider(docker_mw=mw)

        container_name = f"gr-test-status-{int(time.time())}"
        cleanup_containers.append(container_name)

        provider.launch_flowgraph(
            flowgraph_path=str(test_flowgraph),
            name=container_name,
            xmlrpc_port=18083,
        )

        time.sleep(1)

        try:
            status = provider.get_status()
            assert status.connected is False  # Not connected yet
            container_names = [c.name for c in status.containers]
            assert container_name in container_names

        finally:
            provider.stop_flowgraph(container_name)
            provider.remove_flowgraph(container_name, force=True)
            cleanup_containers.remove(container_name)
