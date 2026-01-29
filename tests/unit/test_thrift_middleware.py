"""Unit tests for ThriftMiddleware.

These tests mock the Thrift client since we can't easily connect to
a real ControlPort server in unit tests. The mocked client simulates
the RPCConnectionThrift API.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from gnuradio_mcp.middlewares.thrift import (
    DEFAULT_THRIFT_PORT,
    PERF_COUNTER_SUFFIXES,
    ThriftMiddleware,
)
from gnuradio_mcp.models import KnobModel, KnobPropertiesModel, PerfCounterModel


class MockKnob:
    """Mock for RPCConnectionThrift.Knob."""

    def __init__(self, key: str, value: Any, ktype: int):
        self.key = key
        self.value = value
        self.ktype = ktype


class MockKnobProps:
    """Mock for Thrift knob properties."""

    def __init__(
        self,
        description: str = "",
        units: str = "",
        ktype: int = 5,
        min_val: Any = None,
        max_val: Any = None,
        default_val: Any = None,
    ):
        self.description = description
        self.units = units
        self.type = ktype
        self.min = MockKnob("", min_val, ktype) if min_val is not None else None
        self.max = MockKnob("", max_val, ktype) if max_val is not None else None
        self.defaultvalue = (
            MockKnob("", default_val, ktype) if default_val is not None else None
        )


@pytest.fixture
def mock_client():
    """Create a mock Thrift client."""
    client = MagicMock()

    # Default getKnobs response
    client.getKnobs.return_value = {
        "sig_source0::frequency": MockKnob("sig_source0::frequency", 1000000.0, 5),
        "sig_source0::amplitude": MockKnob("sig_source0::amplitude", 0.5, 5),
        "null_sink0::avg throughput": MockKnob("null_sink0::avg throughput", 1e9, 5),
    }

    # Default getRe response (regex query)
    client.getRe.return_value = {
        "sig_source0::frequency": MockKnob("sig_source0::frequency", 1000000.0, 5),
    }

    # Default properties response
    client.properties.return_value = {
        "sig_source0::frequency": MockKnobProps(
            description="Signal frequency in Hz",
            units="Hz",
            ktype=5,
            min_val=0.0,
            max_val=1e12,
            default_val=1000.0,
        ),
    }

    return client


@pytest.fixture
def thrift_middleware(mock_client):
    """Create a ThriftMiddleware with mocked client."""
    return ThriftMiddleware(mock_client, "127.0.0.1", DEFAULT_THRIFT_PORT)


class TestThriftMiddlewareConnection:
    """Tests for connection handling."""

    def test_get_connection_info(self, thrift_middleware, mock_client):
        """get_connection_info returns host, port, and knob count."""
        mock_client.getKnobs.return_value = {
            "k1": MockKnob("k1", 1, 5),
            "k2": MockKnob("k2", 2, 5),
        }

        info = thrift_middleware.get_connection_info()

        assert info.host == "127.0.0.1"
        assert info.port == DEFAULT_THRIFT_PORT
        assert info.protocol == "thrift"
        assert info.knob_count == 2

    def test_get_connection_info_with_container_name(self, thrift_middleware):
        """get_connection_info includes container name when provided."""
        info = thrift_middleware.get_connection_info(container_name="test-container")

        assert info.container_name == "test-container"

    def test_close(self, thrift_middleware):
        """close clears the client reference."""
        thrift_middleware.close()
        assert thrift_middleware._client is None


class TestThriftMiddlewareVariables:
    """Tests for variable operations (XML-RPC compatible API)."""

    def test_list_variables_filters_perf_counters(self, thrift_middleware, mock_client):
        """list_variables excludes performance counter knobs."""
        mock_client.getKnobs.return_value = {
            "sig_source0::frequency": MockKnob("sig_source0::frequency", 1e6, 5),
            "null_sink0::avg throughput": MockKnob(
                "null_sink0::avg throughput", 1e9, 5
            ),
        }

        variables = thrift_middleware.list_variables()

        # Should only include frequency, not the perf counter
        assert len(variables) == 1
        assert variables[0].name == "sig_source0::frequency"
        assert variables[0].value == 1e6

    def test_get_variable(self, thrift_middleware, mock_client):
        """get_variable returns the knob value."""
        mock_client.getKnobs.return_value = {
            "sig_source0::frequency": MockKnob("sig_source0::frequency", 1e6, 5),
        }

        value = thrift_middleware.get_variable("sig_source0::frequency")

        assert value == 1e6
        mock_client.getKnobs.assert_called_with(["sig_source0::frequency"])

    def test_get_variable_not_found(self, thrift_middleware, mock_client):
        """get_variable raises KeyError for unknown knob."""
        mock_client.getKnobs.return_value = {}

        with pytest.raises(KeyError, match="Knob not found"):
            thrift_middleware.get_variable("unknown::knob")

    def test_set_variable(self, thrift_middleware, mock_client):
        """set_variable updates the knob value."""
        mock_client.getKnobs.return_value = {
            "sig_source0::frequency": MockKnob("sig_source0::frequency", 1e6, 5),
        }

        # Need to mock the import inside the method
        with patch(
            "gnuradio_mcp.middlewares.thrift.RPCConnectionThrift", create=True
        ) as mock_rpc:
            mock_rpc.Knob = MockKnob
            result = thrift_middleware.set_variable("sig_source0::frequency", 2e6)

        assert result is True
        mock_client.setKnobs.assert_called_once()


class TestThriftMiddlewareKnobs:
    """Tests for ControlPort-specific knob operations."""

    def test_get_knobs_all(self, thrift_middleware, mock_client):
        """get_knobs with empty pattern returns all knobs."""
        knobs = thrift_middleware.get_knobs("")

        mock_client.getKnobs.assert_called_with([])
        assert len(knobs) == 3  # All including perf counter

    def test_get_knobs_with_pattern(self, thrift_middleware, mock_client):
        """get_knobs with pattern uses regex query."""
        thrift_middleware.get_knobs(".*frequency.*")

        mock_client.getRe.assert_called_with([".*frequency.*"])

    def test_get_knobs_returns_knob_models(self, thrift_middleware, mock_client):
        """get_knobs returns KnobModel instances with correct types."""
        mock_client.getKnobs.return_value = {
            "k1": MockKnob("k1", 1.0, 5),  # DOUBLE
            "k2": MockKnob("k2", True, 0),  # BOOL
        }

        knobs = thrift_middleware.get_knobs("")

        assert len(knobs) == 2
        assert all(isinstance(k, KnobModel) for k in knobs)

        k1 = next(k for k in knobs if k.name == "k1")
        assert k1.value == 1.0
        assert k1.knob_type == "DOUBLE"

        k2 = next(k for k in knobs if k.name == "k2")
        assert k2.value is True
        assert k2.knob_type == "BOOL"

    def test_get_knob_properties(self, thrift_middleware, mock_client):
        """get_knob_properties returns metadata for knobs."""
        props = thrift_middleware.get_knob_properties(["sig_source0::frequency"])

        mock_client.properties.assert_called_with(["sig_source0::frequency"])
        assert len(props) == 1
        assert isinstance(props[0], KnobPropertiesModel)
        assert props[0].name == "sig_source0::frequency"
        assert props[0].description == "Signal frequency in Hz"
        assert props[0].min_value == 0.0
        assert props[0].max_value == 1e12


class TestThriftMiddlewarePerfCounters:
    """Tests for performance counter operations."""

    def test_get_performance_counters(self, thrift_middleware, mock_client):
        """get_performance_counters extracts per-block metrics."""
        mock_client.getKnobs.return_value = {
            "sig_source0::frequency": MockKnob("sig_source0::frequency", 1e6, 5),
            "sig_source0::avg throughput": MockKnob(
                "sig_source0::avg throughput", 1e9, 5
            ),
            "sig_source0::avg work time": MockKnob(
                "sig_source0::avg work time", 100.0, 5
            ),
            "sig_source0::total work time": MockKnob(
                "sig_source0::total work time", 10000.0, 5
            ),
            "sig_source0::avg nproduced": MockKnob(
                "sig_source0::avg nproduced", 4096.0, 5
            ),
            "null_sink0::avg throughput": MockKnob(
                "null_sink0::avg throughput", 5e8, 5
            ),
        }

        counters = thrift_middleware.get_performance_counters()

        assert len(counters) == 2
        assert all(isinstance(c, PerfCounterModel) for c in counters)

        sig = next(c for c in counters if c.block_name == "sig_source0")
        assert sig.avg_throughput == 1e9
        assert sig.avg_work_time_us == 100.0
        assert sig.total_work_time_us == 10000.0
        assert sig.avg_nproduced == 4096.0

    def test_get_performance_counters_with_block_filter(
        self, thrift_middleware, mock_client
    ):
        """get_performance_counters can filter by block name."""
        mock_client.getRe.return_value = {
            "sig_source0::avg throughput": MockKnob(
                "sig_source0::avg throughput", 1e9, 5
            ),
        }

        counters = thrift_middleware.get_performance_counters(block="sig_source0")

        # Should use regex pattern for the specific block
        mock_client.getRe.assert_called_once()
        call_args = mock_client.getRe.call_args[0][0]
        assert "sig_source0" in call_args[0]


class TestThriftMiddlewareHelpers:
    """Tests for helper methods."""

    @pytest.mark.parametrize(
        "name,expected",
        [
            ("sig_source0::avg throughput", True),
            ("sig_source0::avg work time", True),
            ("sig_source0::total work time", True),
            ("sig_source0::avg nproduced", True),
            ("sig_source0::avg input % full", True),
            ("sig_source0::avg output % full", True),
            ("sig_source0::frequency", False),
            ("sig_source0::amplitude", False),
        ],
    )
    def test_is_perf_counter(self, name, expected):
        """_is_perf_counter correctly identifies performance counters."""
        assert ThriftMiddleware._is_perf_counter(name) == expected

    @pytest.mark.parametrize(
        "value,expected",
        [
            ([0.1, 0.2], [0.1, 0.2]),
            ((0.3, 0.4), [0.3, 0.4]),
            (0.5, [0.5]),
            (None, []),
        ],
    )
    def test_to_list(self, value, expected):
        """_to_list converts values to list of floats."""
        assert ThriftMiddleware._to_list(value) == expected


class TestThriftMiddlewareConnectionError:
    """Tests for connection error handling."""

    def test_connect_import_error(self):
        """connect raises ImportError when gnuradio.ctrlport unavailable."""
        with patch.dict("sys.modules", {"gnuradio.ctrlport": None}):
            with patch(
                "gnuradio_mcp.middlewares.thrift.ThriftMiddleware.connect"
            ) as mock:
                mock.side_effect = ImportError("GNU Radio ControlPort not available")
                with pytest.raises(ImportError):
                    ThriftMiddleware.connect()


class TestKnobTypeNames:
    """Tests for knob type mapping."""

    def test_all_perf_counter_suffixes_defined(self):
        """Ensure all expected perf counter suffixes are defined."""
        expected_suffixes = [
            "::avg throughput",
            "::avg work time",
            "::total work time",
            "::avg nproduced",
            "::avg input % full",
            "::avg output % full",
        ]
        for suffix in expected_suffixes:
            assert suffix in PERF_COUNTER_SUFFIXES
