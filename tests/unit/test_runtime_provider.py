"""Unit tests for RuntimeProvider with mocked middlewares."""

from unittest.mock import MagicMock, patch

import pytest

from gnuradio_mcp.models import (
    ConnectionInfoModel,
    ContainerModel,
    RuntimeStatusModel,
    ScreenshotModel,
    VariableModel,
)
from gnuradio_mcp.providers.runtime import RuntimeProvider


@pytest.fixture
def mock_docker_mw():
    """Mock DockerMiddleware."""
    mw = MagicMock()
    mw.launch.return_value = ContainerModel(
        name="gr-test",
        container_id="abc123",
        status="running",
        flowgraph_path="/path/to/test.grc",
        xmlrpc_port=8080,
    )
    mw.list_containers.return_value = [
        ContainerModel(
            name="gr-test",
            container_id="abc123",
            status="running",
            flowgraph_path="/path/to/test.grc",
            xmlrpc_port=8080,
        )
    ]
    mw.stop.return_value = True
    mw.remove.return_value = True
    mw.get_xmlrpc_port.return_value = 8080
    mw.capture_screenshot.return_value = ScreenshotModel(
        container_name="gr-test",
        image_base64="iVBORw0KGgo=",
        format="png",
    )
    mw.get_logs.return_value = "flowgraph started\n"
    return mw


@pytest.fixture
def mock_xmlrpc_mw():
    """Mock XmlRpcMiddleware."""
    mw = MagicMock()
    mw._url = "http://localhost:8080"
    mw.get_connection_info.return_value = ConnectionInfoModel(
        url="http://localhost:8080",
        xmlrpc_port=8080,
        methods=["get_freq", "set_freq"],
    )
    mw.list_variables.return_value = [
        VariableModel(name="freq", value=1e6),
        VariableModel(name="amp", value=0.5),
    ]
    mw.get_variable.return_value = 1e6
    mw.set_variable.return_value = True
    mw.start.return_value = True
    mw.stop.return_value = True
    mw.lock.return_value = True
    mw.unlock.return_value = True
    return mw


@pytest.fixture
def provider_with_docker(mock_docker_mw):
    """RuntimeProvider with Docker available."""
    return RuntimeProvider(docker_mw=mock_docker_mw)


@pytest.fixture
def provider_no_docker():
    """RuntimeProvider without Docker."""
    return RuntimeProvider(docker_mw=None)


class TestInitialization:
    def test_has_docker_true(self, provider_with_docker):
        assert provider_with_docker._has_docker is True

    def test_has_docker_false(self, provider_no_docker):
        assert provider_no_docker._has_docker is False

    def test_initial_state(self, provider_with_docker):
        assert provider_with_docker._xmlrpc is None
        assert provider_with_docker._active_container is None


class TestPreconditions:
    def test_require_docker_raises_without_docker(self, provider_no_docker):
        with pytest.raises(RuntimeError, match="Docker is not available"):
            provider_no_docker._require_docker()

    def test_require_docker_returns_middleware(self, provider_with_docker, mock_docker_mw):
        result = provider_with_docker._require_docker()
        assert result is mock_docker_mw

    def test_require_xmlrpc_raises_when_not_connected(self, provider_with_docker):
        with pytest.raises(RuntimeError, match="Not connected"):
            provider_with_docker._require_xmlrpc()


class TestContainerLifecycle:
    def test_launch_flowgraph(self, provider_with_docker, mock_docker_mw, tmp_path):
        fg = tmp_path / "test.grc"
        fg.write_text("<flowgraph/>")

        result = provider_with_docker.launch_flowgraph(
            flowgraph_path=str(fg),
            name="my-fg",
            xmlrpc_port=9090,
            enable_vnc=True,
        )

        assert isinstance(result, ContainerModel)
        mock_docker_mw.launch.assert_called_once_with(
            flowgraph_path=str(fg),
            name="my-fg",
            xmlrpc_port=9090,
            enable_vnc=True,
            device_paths=None,
        )

    def test_launch_flowgraph_auto_name(self, provider_with_docker, mock_docker_mw, tmp_path):
        fg = tmp_path / "siggen_xmlrpc.grc"
        fg.write_text("<flowgraph/>")

        provider_with_docker.launch_flowgraph(flowgraph_path=str(fg))

        call_kwargs = mock_docker_mw.launch.call_args
        assert call_kwargs.kwargs["name"] == "gr-siggen_xmlrpc"

    def test_launch_flowgraph_requires_docker(self, provider_no_docker, tmp_path):
        fg = tmp_path / "test.grc"
        fg.write_text("<flowgraph/>")

        with pytest.raises(RuntimeError, match="Docker is not available"):
            provider_no_docker.launch_flowgraph(str(fg))

    def test_list_containers(self, provider_with_docker, mock_docker_mw):
        result = provider_with_docker.list_containers()
        assert len(result) == 1
        assert result[0].name == "gr-test"
        mock_docker_mw.list_containers.assert_called_once()

    def test_stop_flowgraph(self, provider_with_docker, mock_docker_mw):
        result = provider_with_docker.stop_flowgraph("gr-test")
        assert result is True
        mock_docker_mw.stop.assert_called_once_with("gr-test")

    def test_remove_flowgraph(self, provider_with_docker, mock_docker_mw):
        result = provider_with_docker.remove_flowgraph("gr-test", force=True)
        assert result is True
        mock_docker_mw.remove.assert_called_once_with("gr-test", force=True)


class TestConnectionManagement:
    def test_connect(self, provider_with_docker, mock_xmlrpc_mw):
        with patch(
            "gnuradio_mcp.providers.runtime.XmlRpcMiddleware.connect",
            return_value=mock_xmlrpc_mw,
        ):
            result = provider_with_docker.connect("http://localhost:8080")

            assert isinstance(result, ConnectionInfoModel)
            assert provider_with_docker._xmlrpc is mock_xmlrpc_mw
            assert provider_with_docker._active_container is None

    def test_connect_parses_port(self, provider_with_docker, mock_xmlrpc_mw):
        with patch(
            "gnuradio_mcp.providers.runtime.XmlRpcMiddleware.connect",
            return_value=mock_xmlrpc_mw,
        ):
            provider_with_docker.connect("http://localhost:9090")
            mock_xmlrpc_mw.get_connection_info.assert_called_with(xmlrpc_port=9090)

    def test_connect_to_container(self, provider_with_docker, mock_docker_mw, mock_xmlrpc_mw):
        with patch(
            "gnuradio_mcp.providers.runtime.XmlRpcMiddleware.connect",
            return_value=mock_xmlrpc_mw,
        ):
            result = provider_with_docker.connect_to_container("gr-test")

            assert isinstance(result, ConnectionInfoModel)
            assert provider_with_docker._active_container == "gr-test"
            mock_docker_mw.get_xmlrpc_port.assert_called_once_with("gr-test")

    def test_disconnect(self, provider_with_docker, mock_xmlrpc_mw):
        provider_with_docker._xmlrpc = mock_xmlrpc_mw
        provider_with_docker._active_container = "gr-test"

        result = provider_with_docker.disconnect()

        assert result is True
        assert provider_with_docker._xmlrpc is None
        assert provider_with_docker._active_container is None
        mock_xmlrpc_mw.close.assert_called_once()

    def test_disconnect_when_not_connected(self, provider_with_docker):
        result = provider_with_docker.disconnect()
        assert result is True  # Should be idempotent

    def test_get_status_not_connected(self, provider_with_docker, mock_docker_mw):
        result = provider_with_docker.get_status()

        assert isinstance(result, RuntimeStatusModel)
        assert result.connected is False
        assert result.connection is None
        assert len(result.containers) == 1

    def test_get_status_connected(self, provider_with_docker, mock_docker_mw, mock_xmlrpc_mw):
        provider_with_docker._xmlrpc = mock_xmlrpc_mw
        provider_with_docker._active_container = "gr-test"

        result = provider_with_docker.get_status()

        assert result.connected is True
        assert result.connection is not None
        mock_xmlrpc_mw.get_connection_info.assert_called()

    def test_get_status_handles_docker_error(self, provider_with_docker, mock_docker_mw):
        mock_docker_mw.list_containers.side_effect = Exception("Docker error")

        result = provider_with_docker.get_status()

        assert result.containers == []  # Gracefully handles error


class TestVariableControl:
    def test_list_variables(self, provider_with_docker, mock_xmlrpc_mw):
        provider_with_docker._xmlrpc = mock_xmlrpc_mw

        result = provider_with_docker.list_variables()

        assert len(result) == 2
        assert all(isinstance(v, VariableModel) for v in result)
        mock_xmlrpc_mw.list_variables.assert_called_once()

    def test_list_variables_requires_connection(self, provider_with_docker):
        with pytest.raises(RuntimeError, match="Not connected"):
            provider_with_docker.list_variables()

    def test_get_variable(self, provider_with_docker, mock_xmlrpc_mw):
        provider_with_docker._xmlrpc = mock_xmlrpc_mw

        result = provider_with_docker.get_variable("freq")

        assert result == 1e6
        mock_xmlrpc_mw.get_variable.assert_called_once_with("freq")

    def test_set_variable(self, provider_with_docker, mock_xmlrpc_mw):
        provider_with_docker._xmlrpc = mock_xmlrpc_mw

        result = provider_with_docker.set_variable("freq", 2e6)

        assert result is True
        mock_xmlrpc_mw.set_variable.assert_called_once_with("freq", 2e6)


class TestFlowgraphControl:
    def test_start(self, provider_with_docker, mock_xmlrpc_mw):
        provider_with_docker._xmlrpc = mock_xmlrpc_mw
        assert provider_with_docker.start() is True
        mock_xmlrpc_mw.start.assert_called_once()

    def test_stop(self, provider_with_docker, mock_xmlrpc_mw):
        provider_with_docker._xmlrpc = mock_xmlrpc_mw
        assert provider_with_docker.stop() is True
        mock_xmlrpc_mw.stop.assert_called_once()

    def test_lock(self, provider_with_docker, mock_xmlrpc_mw):
        provider_with_docker._xmlrpc = mock_xmlrpc_mw
        assert provider_with_docker.lock() is True
        mock_xmlrpc_mw.lock.assert_called_once()

    def test_unlock(self, provider_with_docker, mock_xmlrpc_mw):
        provider_with_docker._xmlrpc = mock_xmlrpc_mw
        assert provider_with_docker.unlock() is True
        mock_xmlrpc_mw.unlock.assert_called_once()

    def test_flowgraph_control_requires_connection(self, provider_with_docker):
        with pytest.raises(RuntimeError, match="Not connected"):
            provider_with_docker.start()


class TestVisualFeedback:
    def test_capture_screenshot_with_name(self, provider_with_docker, mock_docker_mw):
        result = provider_with_docker.capture_screenshot("gr-test")

        assert isinstance(result, ScreenshotModel)
        mock_docker_mw.capture_screenshot.assert_called_once_with("gr-test")

    def test_capture_screenshot_uses_active_container(self, provider_with_docker, mock_docker_mw):
        provider_with_docker._active_container = "gr-active"

        provider_with_docker.capture_screenshot()

        mock_docker_mw.capture_screenshot.assert_called_once_with("gr-active")

    def test_capture_screenshot_requires_container(self, provider_with_docker):
        with pytest.raises(RuntimeError, match="No container specified"):
            provider_with_docker.capture_screenshot()

    def test_get_container_logs_with_name(self, provider_with_docker, mock_docker_mw):
        result = provider_with_docker.get_container_logs("gr-test", tail=50)

        assert "flowgraph started" in result
        mock_docker_mw.get_logs.assert_called_once_with("gr-test", tail=50)

    def test_get_container_logs_uses_active_container(self, provider_with_docker, mock_docker_mw):
        provider_with_docker._active_container = "gr-active"

        provider_with_docker.get_container_logs()

        mock_docker_mw.get_logs.assert_called_once_with("gr-active", tail=100)

    def test_get_container_logs_requires_container(self, provider_with_docker):
        with pytest.raises(RuntimeError, match="No container specified"):
            provider_with_docker.get_container_logs()
