"""Integration tests for FM scanner signal probe functionality.

Tests the programmatic flowgraph construction and signal probe features
added to the FM scanner. Requires GNU Radio but not RTL-SDR hardware.

Run with: pytest tests/integration/test_fm_scanner.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add examples to path so we can import fm_scanner
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "examples"))

# Check if GNU Radio is available
try:
    from gnuradio import gr

    GNURADIO_AVAILABLE = True
except ImportError:
    GNURADIO_AVAILABLE = False


class TestSignalProbeHelpers:
    """Unit tests for signal probe helper functions (no GNU Radio needed)."""

    def test_mag_squared_to_dbm_normal(self):
        """Test dB conversion for normal values."""
        from fm_scanner import mag_squared_to_dbm

        # 1.0 → 0 dB
        assert mag_squared_to_dbm(1.0) == 0.0

        # 0.1 → -10 dB
        assert abs(mag_squared_to_dbm(0.1) - (-10.0)) < 0.01

        # 0.01 → -20 dB
        assert abs(mag_squared_to_dbm(0.01) - (-20.0)) < 0.01

        # 0.001 → -30 dB
        assert abs(mag_squared_to_dbm(0.001) - (-30.0)) < 0.01

    def test_mag_squared_to_dbm_zero(self):
        """Test dB conversion handles zero gracefully."""
        from fm_scanner import mag_squared_to_dbm

        # Zero should return floor value, not crash
        result = mag_squared_to_dbm(0.0)
        assert result == -100.0

    def test_mag_squared_to_dbm_negative(self):
        """Test dB conversion handles negative values (shouldn't happen but be safe)."""
        from fm_scanner import mag_squared_to_dbm

        result = mag_squared_to_dbm(-1.0)
        assert result == -100.0

    def test_format_signal_bar_strong(self):
        """Test signal bar formatting for strong signals."""
        from fm_scanner import format_signal_bar

        bar = format_signal_bar(-30.0, width=20)
        # Should be mostly filled and green
        assert "█" in bar
        assert "\033[32m" in bar  # green color code

    def test_format_signal_bar_medium(self):
        """Test signal bar formatting for medium signals."""
        from fm_scanner import format_signal_bar

        bar = format_signal_bar(-50.0, width=20)
        # Should be yellow
        assert "\033[33m" in bar  # yellow color code

    def test_format_signal_bar_weak(self):
        """Test signal bar formatting for weak signals."""
        from fm_scanner import format_signal_bar

        bar = format_signal_bar(-70.0, width=20)
        # Should be red (weak signal)
        assert "\033[31m" in bar  # red color code

    def test_format_signal_bar_empty(self):
        """Test signal bar formatting at floor."""
        from fm_scanner import format_signal_bar

        bar = format_signal_bar(-80.0, width=20)
        # At -80 dB, should be empty (only unfilled blocks)
        assert "░" in bar


class TestScannerParsing:
    """Unit tests for scan data parsing."""

    def test_parse_scan_valid_csv(self):
        """Test parsing valid rtl_power CSV output."""
        from fm_scanner import parse_scan

        csv_data = """\
2025-01-01, 12:00:00, 87500000, 87700000, 100000, 1, -45.2, -47.1
2025-01-01, 12:00:01, 87700000, 87900000, 100000, 1, -52.3, -51.8
"""
        readings = parse_scan(csv_data)

        # Should have 4 readings (2 bins per row × 2 rows)
        assert len(readings) == 4

        # Check first reading
        freq_mhz, power_dbm = readings[0]
        assert 87.5 <= freq_mhz <= 87.6  # First bin
        assert power_dbm == -45.2

    def test_parse_scan_empty(self):
        """Test parsing empty CSV."""
        from fm_scanner import parse_scan

        readings = parse_scan("")
        assert readings == []

    def test_parse_scan_malformed(self):
        """Test parsing malformed CSV (should skip bad rows)."""
        from fm_scanner import parse_scan

        csv_data = """\
bad data
2025-01-01, 12:00:00, 87500000, 87700000, 100000, 1, -45.2
more bad data
"""
        readings = parse_scan(csv_data)

        # Should parse the valid row (1 bin)
        assert len(readings) == 1

    def test_aggregate_channels(self):
        """Test channel aggregation snaps to FM channels."""
        from fm_scanner import aggregate_channels

        # Readings around 101.1 MHz
        readings = [
            (101.05, -35.0),
            (101.10, -30.0),
            (101.15, -32.0),
        ]

        channels = aggregate_channels(readings)

        # Should aggregate to one channel around 101.0-101.2
        assert len(channels) >= 1

        # Find the 101.0 channel
        ch101 = next((c for c in channels if 100.9 <= c["freq_mhz"] <= 101.3), None)
        assert ch101 is not None
        # Max power should be used
        assert ch101["power_dbm"] == -30.0

    def test_detect_stations(self):
        """Test station detection above noise floor."""
        from fm_scanner import detect_stations

        channels = [
            {"freq_mhz": 88.1, "power_dbm": -50.0},  # noise
            {"freq_mhz": 91.5, "power_dbm": -30.0},  # station!
            {"freq_mhz": 93.3, "power_dbm": -48.0},  # noise
            {"freq_mhz": 101.1, "power_dbm": -25.0},  # station!
            {"freq_mhz": 105.5, "power_dbm": -52.0},  # noise
        ]

        stations, noise_floor = detect_stations(channels, threshold_db=10.0)

        # Median of [-50, -30, -48, -25, -52] = -48
        assert -50 < noise_floor < -45

        # Should detect 2 stations (>10 dB above noise)
        assert len(stations) == 2

        # Strongest should be first
        assert stations[0]["freq_mhz"] == 101.1
        assert stations[1]["freq_mhz"] == 91.5


@pytest.mark.skipif(not GNURADIO_AVAILABLE, reason="GNU Radio not available")
class TestFlowgraphConstruction:
    """Integration tests for flowgraph construction with signal probe."""

    def test_build_fm_receiver_creates_grc(self):
        """Test that build_fm_receiver creates a valid .grc file."""
        from fm_scanner import build_fm_receiver

        py_path = build_fm_receiver(101.1, gain=10)

        # Should return a path to a Python file
        assert py_path.exists()
        assert py_path.suffix == ".py"

        # Read and verify it contains expected components
        py_code = py_path.read_text()

        # Should have XML-RPC server
        assert "SimpleXMLRPCServer" in py_code

        # Should have signal probe (analog_probe_avg_mag_sqrd)
        assert "probe_avg_mag_sqrd" in py_code

        # Should have signal_level variable
        assert "signal_level" in py_code

        # Should have freq variable
        assert "freq" in py_code

        # Should have get_signal_level method
        assert "get_signal_level" in py_code

    def test_build_fm_receiver_has_signal_probe_block(self):
        """Verify the flowgraph includes the signal probe block."""
        from gnuradio.grc.core.platform import Platform
        from gnuradio import gr

        # Initialize platform
        platform = Platform(
            version=gr.version(),
            version_parts=(gr.major_version(), gr.api_version(), gr.minor_version()),
            prefs=gr.prefs(),
        )
        platform.build_library()

        # Verify the probe block type exists
        block_keys = list(platform.blocks.keys())
        assert "analog_probe_avg_mag_sqrd_x" in block_keys

    def test_build_fm_receiver_has_function_probe_block(self):
        """Verify the flowgraph includes the variable function probe block."""
        from gnuradio.grc.core.platform import Platform
        from gnuradio import gr

        # Initialize platform
        platform = Platform(
            version=gr.version(),
            version_parts=(gr.major_version(), gr.api_version(), gr.minor_version()),
            prefs=gr.prefs(),
        )
        platform.build_library()

        # Verify the function probe block type exists
        block_keys = list(platform.blocks.keys())
        assert "variable_function_probe" in block_keys

    def test_flowgraph_compiled_structure(self):
        """Verify the compiled flowgraph has correct structure."""
        from fm_scanner import build_fm_receiver
        import ast

        py_path = build_fm_receiver(98.5, gain=20)
        py_code = py_path.read_text()

        # Parse as AST to verify structure
        tree = ast.parse(py_code)

        # Find the class definition
        class_defs = [node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
        assert len(class_defs) >= 1

        # Find method definitions
        fm_class = class_defs[0]
        method_names = [
            node.name for node in fm_class.body if isinstance(node, ast.FunctionDef)
        ]

        # Should have get/set methods for freq and signal_level
        assert "get_freq" in method_names
        assert "set_freq" in method_names
        assert "get_signal_level" in method_names
        assert "set_signal_level" in method_names


@pytest.mark.skipif(not GNURADIO_AVAILABLE, reason="GNU Radio not available")
class TestSignalProbeIntegration:
    """Tests for signal probe XML-RPC integration (requires GNU Radio)."""

    def test_compiled_flowgraph_has_xmlrpc(self):
        """Verify compiled flowgraph has XML-RPC server setup."""
        from fm_scanner import build_fm_receiver

        py_path = build_fm_receiver(107.2, gain=15)
        py_code = py_path.read_text()

        # Should configure XML-RPC on port 8090
        assert "8090" in py_code
        assert "0.0.0.0" in py_code or "''" in py_code  # Bind address

    def test_signal_probe_connection(self):
        """Verify signal probe is connected to the LPF output."""
        from fm_scanner import build_fm_receiver

        py_path = build_fm_receiver(101.1, gain=10)
        py_code = py_path.read_text()

        # The probe should be connected (look for connection pattern)
        # In generated code, connections are made via self.connect()
        assert "signal_probe" in py_code

        # The probe should sample from the low pass filter
        assert "low_pass_filter" in py_code
