"""Unit tests for XmlRpcMiddleware with mocked ServerProxy."""

from unittest.mock import MagicMock, patch

import pytest

from gnuradio_mcp.middlewares.xmlrpc import XmlRpcMiddleware
from gnuradio_mcp.models import ConnectionInfoModel, VariableModel


@pytest.fixture
def mock_proxy():
    proxy = MagicMock()
    proxy.system.listMethods.return_value = [
        "system.listMethods",
        "system.methodHelp",
        "get_frequency",
        "set_frequency",
        "get_amplitude",
        "set_amplitude",
        "get_waveform",
        "start",
        "stop",
        "lock",
        "unlock",
    ]
    return proxy


@pytest.fixture
def xmlrpc_mw(mock_proxy):
    return XmlRpcMiddleware(mock_proxy, "http://localhost:8080")


class TestConnect:
    def test_connect_success(self):
        with patch("gnuradio_mcp.middlewares.xmlrpc.xmlrpc.client") as mock_xmlrpc:
            mock_proxy = MagicMock()
            mock_xmlrpc.ServerProxy.return_value = mock_proxy
            mock_xmlrpc.Transport.return_value = MagicMock()

            mw = XmlRpcMiddleware.connect("http://localhost:8080")
            assert mw is not None
            mock_proxy.system.listMethods.assert_called_once()

    def test_connect_failure(self):
        with patch("gnuradio_mcp.middlewares.xmlrpc.xmlrpc.client") as mock_xmlrpc:
            mock_proxy = MagicMock()
            mock_proxy.system.listMethods.side_effect = ConnectionRefusedError()
            mock_xmlrpc.ServerProxy.return_value = mock_proxy
            mock_xmlrpc.Transport.return_value = MagicMock()

            with pytest.raises(ConnectionRefusedError):
                XmlRpcMiddleware.connect("http://localhost:8080")

    def test_connect_without_introspection(self):
        """GRC servers don't enable system.listMethods — connect should still succeed."""
        from xmlrpc.client import Fault

        with patch("gnuradio_mcp.middlewares.xmlrpc.xmlrpc.client") as mock_xmlrpc:
            mock_proxy = MagicMock()
            mock_proxy.system.listMethods.side_effect = Fault(
                1, "method 'system.listMethods' is not supported"
            )
            mock_xmlrpc.ServerProxy.return_value = mock_proxy
            mock_xmlrpc.Transport.return_value = MagicMock()

            mw = XmlRpcMiddleware.connect("http://localhost:8080")
            assert mw is not None


class TestConnectionInfo:
    def test_get_connection_info(self, xmlrpc_mw, mock_proxy):
        result = xmlrpc_mw.get_connection_info(container_name="test", xmlrpc_port=8080)
        assert isinstance(result, ConnectionInfoModel)
        assert result.url == "http://localhost:8080"
        assert result.container_name == "test"
        # Should exclude system.* methods
        assert "system.listMethods" not in result.methods
        assert "get_frequency" in result.methods


class TestListVariables:
    def test_list_variables(self, xmlrpc_mw, mock_proxy):
        mock_proxy.get_frequency.return_value = 1e6
        mock_proxy.get_amplitude.return_value = 0.5

        result = xmlrpc_mw.list_variables()
        assert len(result) == 2
        assert all(isinstance(v, VariableModel) for v in result)

        names = {v.name for v in result}
        assert "frequency" in names
        assert "amplitude" in names
        # waveform has get_ but no set_, should be excluded
        assert "waveform" not in names

    def test_list_variables_with_error(self, xmlrpc_mw, mock_proxy):
        """If get_* fails, variable should still appear with None value."""
        mock_proxy.get_frequency.side_effect = Exception("timeout")
        mock_proxy.get_amplitude.return_value = 0.5

        result = xmlrpc_mw.list_variables()
        freq_var = next(v for v in result if v.name == "frequency")
        assert freq_var.value is None


class TestGetSetVariable:
    def test_get_variable(self, xmlrpc_mw, mock_proxy):
        mock_proxy.get_frequency.return_value = 1e6
        assert xmlrpc_mw.get_variable("frequency") == 1e6

    def test_set_variable(self, xmlrpc_mw, mock_proxy):
        assert xmlrpc_mw.set_variable("frequency", 2e6) is True
        mock_proxy.set_frequency.assert_called_once_with(2e6)


class TestFlowgraphControl:
    def test_start(self, xmlrpc_mw, mock_proxy):
        assert xmlrpc_mw.start() is True
        mock_proxy.start.assert_called_once()

    def test_stop(self, xmlrpc_mw, mock_proxy):
        assert xmlrpc_mw.stop() is True
        mock_proxy.stop.assert_called_once()

    def test_lock(self, xmlrpc_mw, mock_proxy):
        assert xmlrpc_mw.lock() is True
        mock_proxy.lock.assert_called_once()

    def test_unlock(self, xmlrpc_mw, mock_proxy):
        assert xmlrpc_mw.unlock() is True
        mock_proxy.unlock.assert_called_once()
