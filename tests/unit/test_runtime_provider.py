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

    def test_require_docker_returns_middleware(
        self, provider_with_docker, mock_docker_mw
    ):
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
            enable_coverage=False,
            enable_controlport=False,
            controlport_port=9090,
            enable_perf_counters=True,
            device_paths=None,
        )

    def test_launch_flowgraph_auto_name(
        self, provider_with_docker, mock_docker_mw, tmp_path
    ):
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

    def test_connect_to_container(
        self, provider_with_docker, mock_docker_mw, mock_xmlrpc_mw
    ):
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

    def test_get_status_connected(
        self, provider_with_docker, mock_docker_mw, mock_xmlrpc_mw
    ):
        provider_with_docker._xmlrpc = mock_xmlrpc_mw
        provider_with_docker._active_container = "gr-test"

        result = provider_with_docker.get_status()

        assert result.connected is True
        assert result.connection is not None
        mock_xmlrpc_mw.get_connection_info.assert_called()

    def test_get_status_handles_docker_error(
        self, provider_with_docker, mock_docker_mw
    ):
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

    def test_capture_screenshot_uses_active_container(
        self, provider_with_docker, mock_docker_mw
    ):
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

    def test_get_container_logs_uses_active_container(
        self, provider_with_docker, mock_docker_mw
    ):
        provider_with_docker._active_container = "gr-active"

        provider_with_docker.get_container_logs()

        mock_docker_mw.get_logs.assert_called_once_with("gr-active", tail=100)

    def test_get_container_logs_requires_container(self, provider_with_docker):
        with pytest.raises(RuntimeError, match="No container specified"):
            provider_with_docker.get_container_logs()


class TestCoverageCollection:
    """Tests for coverage collection methods."""

    def test_launch_with_coverage(self, provider_with_docker, mock_docker_mw, tmp_path):
        fg = tmp_path / "test.grc"
        fg.write_text("<flowgraph/>")

        provider_with_docker.launch_flowgraph(
            flowgraph_path=str(fg),
            name="cov-test",
            enable_coverage=True,
        )

        mock_docker_mw.launch.assert_called_once()
        call_kwargs = mock_docker_mw.launch.call_args.kwargs
        assert call_kwargs["enable_coverage"] is True

    def test_collect_coverage_no_data(self, provider_with_docker):
        with pytest.raises(FileNotFoundError, match="No coverage data"):
            provider_with_docker.collect_coverage("nonexistent-container")

    def test_collect_coverage_success(
        self, provider_with_docker, tmp_path, monkeypatch
    ):
        from gnuradio_mcp.models import CoverageDataModel

        # Create fake coverage directory and file
        monkeypatch.setattr(
            "gnuradio_mcp.providers.runtime.HOST_COVERAGE_BASE", str(tmp_path)
        )
        coverage_dir = tmp_path / "test-container"
        coverage_dir.mkdir()
        (coverage_dir / ".coverage").write_bytes(b"fake coverage data")

        # Mock subprocess to return fake coverage report
        def mock_run(cmd, **kwargs):
            class FakeResult:
                stdout = """Name          Stmts   Miss Branch BrPart  Cover
-----------------------------------------------
module.py        100     20     40     10    75%
-----------------------------------------------
TOTAL            100     20     40     10    75%"""
                stderr = ""
                returncode = 0

            return FakeResult()

        monkeypatch.setattr("subprocess.run", mock_run)

        result = provider_with_docker.collect_coverage("test-container")

        assert isinstance(result, CoverageDataModel)
        assert result.container_name == "test-container"
        assert result.coverage_percent == 75.0
        assert result.lines_total == 100
        assert result.lines_covered == 80  # 100 - 20 missed

    def test_generate_coverage_report_html(
        self, provider_with_docker, tmp_path, monkeypatch
    ):
        from gnuradio_mcp.models import CoverageReportModel

        # Setup
        monkeypatch.setattr(
            "gnuradio_mcp.providers.runtime.HOST_COVERAGE_BASE", str(tmp_path)
        )
        coverage_dir = tmp_path / "test-container"
        coverage_dir.mkdir()
        (coverage_dir / ".coverage").write_bytes(b"fake coverage data")

        # Mock subprocess
        def mock_run(cmd, **kwargs):
            class FakeResult:
                returncode = 0

            # Create output file for HTML
            if "html" in cmd:
                html_dir = coverage_dir / "htmlcov"
                html_dir.mkdir(exist_ok=True)
                (html_dir / "index.html").write_text("<html>Coverage</html>")
            return FakeResult()

        monkeypatch.setattr("subprocess.run", mock_run)

        result = provider_with_docker.generate_coverage_report("test-container", "html")

        assert isinstance(result, CoverageReportModel)
        assert result.format == "html"
        assert "htmlcov" in result.report_path

    def test_generate_coverage_report_xml(
        self, provider_with_docker, tmp_path, monkeypatch
    ):
        from gnuradio_mcp.models import CoverageReportModel

        monkeypatch.setattr(
            "gnuradio_mcp.providers.runtime.HOST_COVERAGE_BASE", str(tmp_path)
        )
        coverage_dir = tmp_path / "test-container"
        coverage_dir.mkdir()
        (coverage_dir / ".coverage").write_bytes(b"fake coverage data")

        def mock_run(cmd, **kwargs):
            class FakeResult:
                returncode = 0

            return FakeResult()

        monkeypatch.setattr("subprocess.run", mock_run)

        result = provider_with_docker.generate_coverage_report("test-container", "xml")

        assert isinstance(result, CoverageReportModel)
        assert result.format == "xml"
        assert "coverage.xml" in result.report_path

    def test_generate_coverage_report_requires_coverage_file(
        self, provider_with_docker, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            "gnuradio_mcp.providers.runtime.HOST_COVERAGE_BASE", str(tmp_path)
        )
        coverage_dir = tmp_path / "test-container"
        coverage_dir.mkdir()
        # No .coverage file

        with pytest.raises(FileNotFoundError, match="No combined coverage file"):
            provider_with_docker.generate_coverage_report("test-container", "html")

    def test_combine_coverage(self, provider_with_docker, tmp_path, monkeypatch):
        from gnuradio_mcp.models import CoverageDataModel

        monkeypatch.setattr(
            "gnuradio_mcp.providers.runtime.HOST_COVERAGE_BASE", str(tmp_path)
        )

        # Create two containers with coverage data
        for name in ["container-1", "container-2"]:
            coverage_dir = tmp_path / name
            coverage_dir.mkdir()
            (coverage_dir / ".coverage").write_bytes(b"fake coverage")

        def mock_run(cmd, **kwargs):
            class FakeResult:
                stdout = "TOTAL            200     40     80     20    75%"
                stderr = ""
                returncode = 0

            # Create combined coverage file
            if "combine" in cmd:
                combined_dir = tmp_path / "combined"
                combined_dir.mkdir(exist_ok=True)
                (combined_dir / ".coverage").write_bytes(b"combined data")
            return FakeResult()

        monkeypatch.setattr("subprocess.run", mock_run)

        result = provider_with_docker.combine_coverage(["container-1", "container-2"])

        assert isinstance(result, CoverageDataModel)
        assert result.container_name == "combined"

    def test_combine_coverage_requires_names(self, provider_with_docker):
        with pytest.raises(ValueError, match="At least one container"):
            provider_with_docker.combine_coverage([])

    def test_delete_coverage_specific(
        self, provider_with_docker, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            "gnuradio_mcp.providers.runtime.HOST_COVERAGE_BASE", str(tmp_path)
        )

        # Create coverage directory
        coverage_dir = tmp_path / "test-container"
        coverage_dir.mkdir()
        (coverage_dir / ".coverage").write_bytes(b"data")

        deleted = provider_with_docker.delete_coverage(name="test-container")

        assert deleted == 1
        assert not coverage_dir.exists()

    def test_delete_coverage_older_than(
        self, provider_with_docker, tmp_path, monkeypatch
    ):
        import os
        import time

        monkeypatch.setattr(
            "gnuradio_mcp.providers.runtime.HOST_COVERAGE_BASE", str(tmp_path)
        )

        # Create old and new coverage directories
        old_dir = tmp_path / "old-container"
        old_dir.mkdir()
        # Set mtime to 10 days ago
        old_time = time.time() - (10 * 86400)
        os.utime(old_dir, (old_time, old_time))

        new_dir = tmp_path / "new-container"
        new_dir.mkdir()

        deleted = provider_with_docker.delete_coverage(older_than_days=7)

        assert deleted == 1
        assert not old_dir.exists()
        assert new_dir.exists()

    def test_delete_coverage_all(self, provider_with_docker, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "gnuradio_mcp.providers.runtime.HOST_COVERAGE_BASE", str(tmp_path)
        )

        # Create multiple directories
        (tmp_path / "container-1").mkdir()
        (tmp_path / "container-2").mkdir()

        deleted = provider_with_docker.delete_coverage()

        assert deleted == 2
        assert not (tmp_path / "container-1").exists()
        assert not (tmp_path / "container-2").exists()

    def test_delete_coverage_nonexistent(
        self, provider_with_docker, tmp_path, monkeypatch
    ):
        monkeypatch.setattr(
            "gnuradio_mcp.providers.runtime.HOST_COVERAGE_BASE", str(tmp_path)
        )
        # tmp_path exists but is empty

        deleted = provider_with_docker.delete_coverage(name="nonexistent")
        assert deleted == 0

    def test_parse_coverage_summary(self, provider_with_docker):
        summary = """Name          Stmts   Miss Branch BrPart  Cover
-----------------------------------------------
module.py        150     30     60     15    80%
other.py          50     20     20      5    60%
-----------------------------------------------
TOTAL            200     50     80     20    75%"""

        metrics = provider_with_docker._parse_coverage_summary(summary)

        assert metrics["lines_total"] == 200
        assert metrics["lines_covered"] == 150  # 200 - 50 missed
        assert metrics["coverage_percent"] == 75.0

    def test_parse_coverage_summary_no_total(self, provider_with_docker):
        summary = "No coverage data collected"

        metrics = provider_with_docker._parse_coverage_summary(summary)

        assert metrics["lines_total"] is None
        assert metrics["lines_covered"] is None
        assert metrics["coverage_percent"] is None
