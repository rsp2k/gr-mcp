"""Unit tests for DockerMiddleware with mocked Docker client."""

import socket
from unittest.mock import MagicMock, patch

import pytest

from gnuradio_mcp.middlewares.docker import (
    DEFAULT_XMLRPC_PORT,
    DockerMiddleware,
)
from gnuradio_mcp.middlewares.ports import PortConflictError
from gnuradio_mcp.models import ContainerModel, ScreenshotModel


@pytest.fixture
def mock_docker_client():
    return MagicMock()


@pytest.fixture
def docker_mw(mock_docker_client):
    return DockerMiddleware(mock_docker_client)


class TestDockerMiddlewareCreate:
    def test_create_returns_none_when_docker_unavailable(self):
        with patch(
            "gnuradio_mcp.middlewares.docker.docker",
            create=True,
        ) as mock_mod:
            mock_mod.from_env.side_effect = Exception("No Docker")
            # We need to patch the import inside create()
            with patch.dict("sys.modules", {"docker": mock_mod}):
                result = DockerMiddleware.create()
                assert result is None

    def test_create_returns_middleware_when_docker_available(self):
        mock_mod = MagicMock()
        mock_client = MagicMock()
        mock_mod.from_env.return_value = mock_client
        with patch.dict("sys.modules", {"docker": mock_mod}):
            result = DockerMiddleware.create()
            assert result is not None
            mock_client.ping.assert_called_once()


class TestLaunch:
    @pytest.fixture(autouse=True)
    def _bypass_port_check(self):
        """Existing launch tests don't care about port availability."""
        with patch(
            "gnuradio_mcp.middlewares.docker.is_port_available", return_value=True
        ):
            yield

    def test_launch_creates_container(self, docker_mw, mock_docker_client, tmp_path):
        fg_file = tmp_path / "test.grc"
        fg_file.write_text("<flowgraph/>")

        mock_container = MagicMock()
        mock_container.id = "abc123def456"
        mock_docker_client.containers.run.return_value = mock_container

        result = docker_mw.launch(
            flowgraph_path=str(fg_file),
            name="test-fg",
            xmlrpc_port=8080,
        )

        assert isinstance(result, ContainerModel)
        assert result.name == "test-fg"
        assert result.container_id == "abc123def456"
        assert result.status == "running"
        assert result.xmlrpc_port == 8080

        mock_docker_client.containers.run.assert_called_once()
        call_kwargs = mock_docker_client.containers.run.call_args
        assert call_kwargs.kwargs["name"] == "test-fg"
        assert call_kwargs.kwargs["detach"] is True

    def test_launch_raises_on_missing_file(self, docker_mw):
        with pytest.raises(FileNotFoundError):
            docker_mw.launch(
                flowgraph_path="/nonexistent/file.grc",
                name="test",
            )

    def test_launch_with_vnc(self, docker_mw, mock_docker_client, tmp_path):
        fg_file = tmp_path / "test.grc"
        fg_file.write_text("<flowgraph/>")

        mock_container = MagicMock()
        mock_container.id = "abc123def456"
        mock_docker_client.containers.run.return_value = mock_container

        result = docker_mw.launch(
            flowgraph_path=str(fg_file),
            name="test-vnc",
            enable_vnc=True,
        )
        assert result.vnc_port == 5900

        # Verify VNC label is set
        call_kwargs = mock_docker_client.containers.run.call_args
        assert call_kwargs.kwargs["labels"]["gr-mcp.vnc-enabled"] == "1"

    def test_launch_without_vnc_sets_label(
        self, docker_mw, mock_docker_client, tmp_path
    ):
        fg_file = tmp_path / "test.grc"
        fg_file.write_text("<flowgraph/>")

        mock_container = MagicMock()
        mock_container.id = "abc123def456"
        mock_docker_client.containers.run.return_value = mock_container

        result = docker_mw.launch(
            flowgraph_path=str(fg_file),
            name="test-no-vnc",
            enable_vnc=False,
        )
        assert result.vnc_port is None

        # Verify VNC label is explicitly set to "0"
        call_kwargs = mock_docker_client.containers.run.call_args
        assert call_kwargs.kwargs["labels"]["gr-mcp.vnc-enabled"] == "0"

    def test_launch_with_devices(self, docker_mw, mock_docker_client, tmp_path):
        fg_file = tmp_path / "test.grc"
        fg_file.write_text("<flowgraph/>")

        mock_container = MagicMock()
        mock_container.id = "abc123def456"
        mock_docker_client.containers.run.return_value = mock_container

        result = docker_mw.launch(
            flowgraph_path=str(fg_file),
            name="test-sdr",
            device_paths=["/dev/bus/usb/001/002"],
        )
        assert result.device_paths == ["/dev/bus/usb/001/002"]

        call_kwargs = mock_docker_client.containers.run.call_args
        assert (
            "/dev/bus/usb/001/002:/dev/bus/usb/001/002:rwm"
            in call_kwargs.kwargs["devices"]
        )


class TestListContainers:
    def test_list_containers(self, docker_mw, mock_docker_client):
        mock_c = MagicMock()
        mock_c.name = "gr-test"
        mock_c.id = "abc123def456"
        mock_c.status = "running"
        mock_c.labels = {
            "gr-mcp.flowgraph": "/path/to/test.grc",
            "gr-mcp.xmlrpc-port": "8080",
            "gr-mcp.vnc-enabled": "0",
        }
        mock_docker_client.containers.list.return_value = [mock_c]

        result = docker_mw.list_containers()
        assert len(result) == 1
        assert result[0].name == "gr-test"
        assert result[0].flowgraph_path == "/path/to/test.grc"
        assert result[0].vnc_port is None  # VNC not enabled

    def test_list_containers_with_vnc(self, docker_mw, mock_docker_client):
        mock_c = MagicMock()
        mock_c.name = "gr-test-vnc"
        mock_c.id = "abc123def456"
        mock_c.status = "running"
        mock_c.labels = {
            "gr-mcp.flowgraph": "/path/to/test.grc",
            "gr-mcp.xmlrpc-port": "8080",
            "gr-mcp.vnc-enabled": "1",
        }
        mock_docker_client.containers.list.return_value = [mock_c]

        result = docker_mw.list_containers()
        assert len(result) == 1
        assert result[0].vnc_port == 5900  # VNC enabled

    def test_list_containers_empty(self, docker_mw, mock_docker_client):
        mock_docker_client.containers.list.return_value = []
        result = docker_mw.list_containers()
        assert result == []


class TestStopRemove:
    def test_stop(self, docker_mw, mock_docker_client):
        mock_container = MagicMock()
        mock_docker_client.containers.get.return_value = mock_container
        assert docker_mw.stop("test") is True
        # Default timeout is 30s for graceful shutdown (coverage needs time)
        mock_container.stop.assert_called_once_with(timeout=30)

    def test_remove(self, docker_mw, mock_docker_client):
        mock_container = MagicMock()
        mock_docker_client.containers.get.return_value = mock_container
        assert docker_mw.remove("test") is True
        mock_container.remove.assert_called_once_with(force=False)

    def test_remove_force(self, docker_mw, mock_docker_client):
        mock_container = MagicMock()
        mock_docker_client.containers.get.return_value = mock_container
        assert docker_mw.remove("test", force=True) is True
        mock_container.remove.assert_called_once_with(force=True)


class TestLogs:
    def test_get_logs(self, docker_mw, mock_docker_client):
        mock_container = MagicMock()
        mock_container.logs.return_value = b"flowgraph started\n"
        mock_docker_client.containers.get.return_value = mock_container

        result = docker_mw.get_logs("test", tail=50)
        assert "flowgraph started" in result
        mock_container.logs.assert_called_once_with(tail=50)


class TestScreenshot:
    def test_capture_screenshot(self, docker_mw, mock_docker_client):
        mock_container = MagicMock()
        # Simulate PNG bytes
        mock_container.exec_run.return_value = (0, b"\x89PNG\r\n\x1a\n")
        mock_docker_client.containers.get.return_value = mock_container

        result = docker_mw.capture_screenshot("test")
        assert isinstance(result, ScreenshotModel)
        assert result.container_name == "test"
        assert result.format == "png"
        assert len(result.image_base64) > 0

    def test_capture_screenshot_failure(self, docker_mw, mock_docker_client):
        mock_container = MagicMock()
        mock_container.exec_run.return_value = (1, b"error: no display")
        mock_docker_client.containers.get.return_value = mock_container

        with pytest.raises(RuntimeError, match="Screenshot failed"):
            docker_mw.capture_screenshot("test")


class TestGetXmlRpcPort:
    def test_get_port_from_label(self, docker_mw, mock_docker_client):
        mock_container = MagicMock()
        mock_container.labels = {"gr-mcp.xmlrpc-port": "9090"}
        mock_docker_client.containers.get.return_value = mock_container

        assert docker_mw.get_xmlrpc_port("test") == 9090

    def test_get_default_port(self, docker_mw, mock_docker_client):
        mock_container = MagicMock()
        mock_container.labels = {}
        mock_docker_client.containers.get.return_value = mock_container

        assert docker_mw.get_xmlrpc_port("test") == DEFAULT_XMLRPC_PORT


class TestCoverage:
    @pytest.fixture(autouse=True)
    def _bypass_port_check(self):
        with patch(
            "gnuradio_mcp.middlewares.docker.is_port_available", return_value=True
        ):
            yield

    def test_launch_with_coverage_uses_coverage_image(
        self, docker_mw, mock_docker_client, tmp_path
    ):
        from gnuradio_mcp.middlewares.docker import COVERAGE_IMAGE, RUNTIME_IMAGE

        fg_file = tmp_path / "test.grc"
        fg_file.write_text("<flowgraph/>")

        mock_container = MagicMock()
        mock_container.id = "abc123def456"
        mock_docker_client.containers.run.return_value = mock_container

        # Without coverage
        docker_mw.launch(str(fg_file), "test-no-cov", enable_coverage=False)
        call_args = mock_docker_client.containers.run.call_args
        assert call_args.args[0] == RUNTIME_IMAGE

        mock_docker_client.reset_mock()

        # With coverage
        docker_mw.launch(str(fg_file), "test-with-cov", enable_coverage=True)
        call_args = mock_docker_client.containers.run.call_args
        assert call_args.args[0] == COVERAGE_IMAGE

    def test_launch_with_coverage_sets_env_and_label(
        self, docker_mw, mock_docker_client, tmp_path
    ):
        fg_file = tmp_path / "test.grc"
        fg_file.write_text("<flowgraph/>")

        mock_container = MagicMock()
        mock_container.id = "abc123def456"
        mock_docker_client.containers.run.return_value = mock_container

        result = docker_mw.launch(str(fg_file), "test-cov", enable_coverage=True)

        call_kwargs = mock_docker_client.containers.run.call_args.kwargs
        assert call_kwargs["environment"]["ENABLE_COVERAGE"] == "1"
        assert call_kwargs["labels"]["gr-mcp.coverage-enabled"] == "1"
        assert result.coverage_enabled is True

    def test_launch_with_coverage_mounts_coverage_dir(
        self, docker_mw, mock_docker_client, tmp_path
    ):
        from gnuradio_mcp.middlewares.docker import (
            CONTAINER_COVERAGE_DIR,
            HOST_COVERAGE_BASE,
        )

        fg_file = tmp_path / "test.grc"
        fg_file.write_text("<flowgraph/>")

        mock_container = MagicMock()
        mock_container.id = "abc123def456"
        mock_docker_client.containers.run.return_value = mock_container

        docker_mw.launch(str(fg_file), "test-cov-mount", enable_coverage=True)

        call_kwargs = mock_docker_client.containers.run.call_args.kwargs
        volumes = call_kwargs["volumes"]
        # Coverage directory should be mounted
        coverage_host_path = f"{HOST_COVERAGE_BASE}/test-cov-mount"
        assert coverage_host_path in volumes
        assert volumes[coverage_host_path]["bind"] == CONTAINER_COVERAGE_DIR
        assert volumes[coverage_host_path]["mode"] == "rw"

    def test_list_containers_includes_coverage_enabled(
        self, docker_mw, mock_docker_client
    ):
        mock_container_cov = MagicMock()
        mock_container_cov.name = "with-cov"
        mock_container_cov.id = "aaa111"
        mock_container_cov.status = "running"
        mock_container_cov.labels = {
            "gr-mcp.flowgraph": "/test.grc",
            "gr-mcp.xmlrpc-port": "8080",
            "gr-mcp.vnc-enabled": "0",
            "gr-mcp.coverage-enabled": "1",
        }

        mock_container_no_cov = MagicMock()
        mock_container_no_cov.name = "no-cov"
        mock_container_no_cov.id = "bbb222"
        mock_container_no_cov.status = "running"
        mock_container_no_cov.labels = {
            "gr-mcp.flowgraph": "/test2.grc",
            "gr-mcp.xmlrpc-port": "8081",
            "gr-mcp.vnc-enabled": "0",
            "gr-mcp.coverage-enabled": "0",
        }

        mock_docker_client.containers.list.return_value = [
            mock_container_cov,
            mock_container_no_cov,
        ]

        result = docker_mw.list_containers()
        assert len(result) == 2
        assert result[0].coverage_enabled is True
        assert result[1].coverage_enabled is False

    def test_is_coverage_enabled(self, docker_mw, mock_docker_client):
        mock_container = MagicMock()
        mock_container.labels = {"gr-mcp.coverage-enabled": "1"}
        mock_docker_client.containers.get.return_value = mock_container

        assert docker_mw.is_coverage_enabled("test") is True

        mock_container.labels = {"gr-mcp.coverage-enabled": "0"}
        assert docker_mw.is_coverage_enabled("test") is False

        mock_container.labels = {}
        assert docker_mw.is_coverage_enabled("test") is False

    def test_get_coverage_dir(self, docker_mw):
        from pathlib import Path

        from gnuradio_mcp.middlewares.docker import HOST_COVERAGE_BASE

        result = docker_mw.get_coverage_dir("my-container")
        expected = Path(HOST_COVERAGE_BASE) / "my-container"
        assert result == expected

    def test_stop_with_timeout_warning(self, docker_mw, mock_docker_client, caplog):
        import logging

        mock_container = MagicMock()
        mock_container.stop.side_effect = Exception("Timeout waiting for container")
        mock_docker_client.containers.get.return_value = mock_container

        with caplog.at_level(logging.WARNING):
            result = docker_mw.stop("test")

        # Should still return True (container will be killed)
        assert result is True
        assert "didn't stop gracefully" in caplog.text


# Sample flowgraph with embedded XML-RPC port
_SAMPLE_FG = """\
#!/usr/bin/env python3
from xmlrpc.server import SimpleXMLRPCServer
class top_block:
    def __init__(self):
        self.xmlrpc_server_0 = SimpleXMLRPCServer(('localhost', 8080), allow_none=True)
"""


class TestPortAllocation:
    def test_launch_auto_allocates_port(self, docker_mw, mock_docker_client, tmp_path):
        """xmlrpc_port=0 should auto-allocate a free port."""
        fg_file = tmp_path / "test.py"
        fg_file.write_text(_SAMPLE_FG)

        mock_container = MagicMock()
        mock_container.id = "abc123def456"
        mock_docker_client.containers.run.return_value = mock_container

        result = docker_mw.launch(
            flowgraph_path=str(fg_file),
            name="test-auto",
            xmlrpc_port=0,
        )
        # Auto-allocated port should be > 0 and not the default
        assert result.xmlrpc_port > 0

    def test_launch_occupied_port_raises(self, docker_mw, mock_docker_client, tmp_path):
        """Requesting a port that's already in use should raise PortConflictError."""
        fg_file = tmp_path / "test.py"
        fg_file.write_text(_SAMPLE_FG)

        # Hold a port open
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.bind(("127.0.0.1", 0))
            occupied_port = s.getsockname()[1]

            with pytest.raises(PortConflictError, match="already in use"):
                docker_mw.launch(
                    flowgraph_path=str(fg_file),
                    name="test-conflict",
                    xmlrpc_port=occupied_port,
                )

    def test_launch_patches_mismatched_port(
        self, docker_mw, mock_docker_client, tmp_path
    ):
        """When flowgraph has port 8080 but we request 9999, it should be patched."""
        fg_file = tmp_path / "flowgraph.py"
        fg_file.write_text(_SAMPLE_FG)

        mock_container = MagicMock()
        mock_container.id = "abc123def456"
        mock_docker_client.containers.run.return_value = mock_container

        # Use a port we know is free (mock is_port_available for determinism)
        with patch(
            "gnuradio_mcp.middlewares.docker.is_port_available", return_value=True
        ):
            result = docker_mw.launch(
                flowgraph_path=str(fg_file),
                name="test-patch",
                xmlrpc_port=9999,
            )

        assert result.xmlrpc_port == 9999

        # Original file should be unchanged
        assert "8080" in fg_file.read_text()

    def test_launch_compat_patch_when_ports_match(
        self, docker_mw, mock_docker_client, tmp_path
    ):
        """When ports match, port is unchanged but compat patches still apply."""
        fg_file = tmp_path / "flowgraph.py"
        fg_file.write_text(_SAMPLE_FG)

        mock_container = MagicMock()
        mock_container.id = "abc123def456"
        mock_docker_client.containers.run.return_value = mock_container

        with patch(
            "gnuradio_mcp.middlewares.docker.is_port_available", return_value=True
        ):
            result = docker_mw.launch(
                flowgraph_path=str(fg_file),
                name="test-match",
                xmlrpc_port=8080,
            )

        assert result.xmlrpc_port == 8080
        # Compat patches (localhost→0.0.0.0) create a patched file even
        # when the port matches, so we expect 2 .py files.
        py_files = list(tmp_path.glob("*.py"))
        assert len(py_files) == 2
        patched = [f for f in py_files if "patched" in f.name]
        assert len(patched) == 1
        # Port unchanged, but localhost rewritten to 0.0.0.0
        patched_text = patched[0].read_text()
        assert "8080" in patched_text
        assert "'0.0.0.0'" in patched_text
