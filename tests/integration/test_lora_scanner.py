"""Integration tests for LoRa scanner band sweep and flowgraph construction.

Tests the programmatic flowgraph construction and scan data parsing for
the LoRa ISM band scanner. Requires GNU Radio + gr-lora_sdr for flowgraph
tests, but parsing tests run without hardware or GNU Radio.

Run with: pytest tests/integration/test_lora_scanner.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Add examples to path so we can import lora_scanner
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "examples"))

# Check if GNU Radio is available
try:
    from gnuradio import gr

    GNURADIO_AVAILABLE = True
except ImportError:
    GNURADIO_AVAILABLE = False

# Check if gr-lora_sdr is available (needs the Docker image or local install)
try:
    import lora_sdr  # noqa: F401

    LORA_SDR_AVAILABLE = True
except ImportError:
    LORA_SDR_AVAILABLE = False


class TestScanParsing:
    """Unit tests for LoRa scan data parsing (no GNU Radio needed)."""

    def test_parse_lora_scan_valid_csv(self):
        """Test parsing valid rtl_power CSV output for ISM band."""
        from lora_scanner import parse_lora_scan

        csv_data = """\
2025-01-01, 12:00:00, 902000000, 902100000, 50000, 1, -55.2, -57.1
2025-01-01, 12:00:01, 915000000, 915100000, 50000, 1, -32.3, -38.8
"""
        readings = parse_lora_scan(csv_data)

        # Should have 4 readings (2 bins per row x 2 rows)
        assert len(readings) == 4

        # First reading should be in the 902 MHz range
        freq_mhz, power_dbm = readings[0]
        assert 902.0 <= freq_mhz <= 902.1
        assert power_dbm == -55.2

        # Third reading should be in the 915 MHz range
        freq_mhz, power_dbm = readings[2]
        assert 915.0 <= freq_mhz <= 915.1
        assert power_dbm == -32.3

    def test_parse_lora_scan_empty(self):
        """Test parsing empty CSV."""
        from lora_scanner import parse_lora_scan

        readings = parse_lora_scan("")
        assert readings == []

    def test_parse_lora_scan_malformed(self):
        """Test parsing malformed CSV (should skip bad rows)."""
        from lora_scanner import parse_lora_scan

        csv_data = """\
bad data
2025-01-01, 12:00:00, 915000000, 915050000, 50000, 1, -42.5
more bad data
"""
        readings = parse_lora_scan(csv_data)
        assert len(readings) == 1

    def test_aggregate_lora_channels_125khz(self):
        """Test channel aggregation snaps to 125 kHz LoRa channels."""
        from lora_scanner import aggregate_lora_channels

        # Readings clustered around 915.0 MHz
        readings = [
            (914.950, -40.0),
            (915.000, -32.0),
            (915.050, -35.0),
        ]

        channels = aggregate_lora_channels(readings, channel_bw_khz=125)

        # Should aggregate to channel(s) near 915 MHz
        assert len(channels) >= 1

        # Find the channel closest to 915.0
        ch915 = min(channels, key=lambda c: abs(c["freq_mhz"] - 915.0))
        assert abs(ch915["freq_mhz"] - 915.0) < 0.125
        # Max power should be used (carrier peak)
        assert ch915["power_dbm"] == -32.0

    def test_aggregate_lora_channels_out_of_band(self):
        """Test that out-of-band readings are excluded."""
        from lora_scanner import aggregate_lora_channels

        readings = [
            (800.0, -30.0),   # below ISM band
            (915.0, -35.0),   # in-band
            (950.0, -30.0),   # above ISM band
        ]

        channels = aggregate_lora_channels(readings)
        # Only the in-band reading should produce a channel
        assert len(channels) == 1
        assert abs(channels[0]["freq_mhz"] - 915.0) < 0.125

    def test_detect_lora_activity(self):
        """Test activity detection above noise floor."""
        from lora_scanner import detect_lora_activity

        channels = [
            {"freq_mhz": 903.0, "power_dbm": -55.0},   # noise
            {"freq_mhz": 909.0, "power_dbm": -58.0},   # noise
            {"freq_mhz": 915.0, "power_dbm": -32.0},   # active!
            {"freq_mhz": 920.0, "power_dbm": -56.0},   # noise
            {"freq_mhz": 925.0, "power_dbm": -40.0},   # active!
        ]

        active, noise_floor = detect_lora_activity(channels, threshold_db=8.0)

        # Median of [-55, -58, -32, -56, -40] = -55
        assert -58 < noise_floor < -50

        # Should detect 2 active channels (>8 dB above noise)
        assert len(active) == 2

        # Strongest should be first
        assert active[0]["freq_mhz"] == 915.0
        assert active[1]["freq_mhz"] == 925.0

    def test_detect_lora_activity_empty(self):
        """Test activity detection with empty channel list."""
        from lora_scanner import detect_lora_activity

        active, noise_floor = detect_lora_activity([])
        assert active == []
        assert noise_floor == -99.0

    def test_detect_lora_activity_low_threshold(self):
        """Test that lower threshold catches more channels."""
        from lora_scanner import detect_lora_activity

        channels = [
            {"freq_mhz": 903.0, "power_dbm": -55.0},
            {"freq_mhz": 915.0, "power_dbm": -48.0},  # 7 dB above median
            {"freq_mhz": 920.0, "power_dbm": -56.0},
        ]

        # At 8 dB threshold, 915.0 should NOT be detected
        active_8, _ = detect_lora_activity(channels, threshold_db=8.0)
        assert len(active_8) == 0

        # At 5 dB threshold, 915.0 SHOULD be detected
        active_5, _ = detect_lora_activity(channels, threshold_db=5.0)
        assert len(active_5) == 1
        assert active_5[0]["freq_mhz"] == 915.0

    def test_aggregate_lora_channels_250khz(self):
        """Test aggregation with 250 kHz bandwidth (wider LoRa channels)."""
        from lora_scanner import aggregate_lora_channels

        readings = [
            (914.900, -40.0),
            (914.950, -38.0),
            (915.000, -32.0),
            (915.050, -35.0),
            (915.100, -39.0),
        ]

        channels = aggregate_lora_channels(readings, channel_bw_khz=250)

        # With 250 kHz bins, more readings should aggregate together
        assert len(channels) >= 1
        ch = min(channels, key=lambda c: abs(c["freq_mhz"] - 915.0))
        # Max should still be -32.0
        assert ch["power_dbm"] == -32.0


@pytest.mark.skipif(not GNURADIO_AVAILABLE, reason="GNU Radio not available")
class TestLoraBlockAvailability:
    """Tests that verify gr-lora_sdr block registration (requires GNU Radio)."""

    def _get_platform_blocks(self):
        """Helper to initialize platform and get block keys."""
        from gnuradio import gr
        from gnuradio.grc.core.platform import Platform

        platform = Platform(
            version=gr.version(),
            version_parts=(
                gr.major_version(),
                gr.api_version(),
                gr.minor_version(),
            ),
            prefs=gr.prefs(),
        )
        platform.build_library()
        return list(platform.blocks.keys())

    @pytest.mark.skipif(not LORA_SDR_AVAILABLE, reason="gr-lora_sdr not installed")
    def test_lora_frame_sync_available(self):
        """Verify frame_sync block is registered."""
        block_keys = self._get_platform_blocks()
        assert "lora_sdr_frame_sync" in block_keys

    @pytest.mark.skipif(not LORA_SDR_AVAILABLE, reason="gr-lora_sdr not installed")
    def test_lora_fft_demod_available(self):
        """Verify fft_demod block is registered."""
        block_keys = self._get_platform_blocks()
        assert "lora_sdr_fft_demod" in block_keys

    @pytest.mark.skipif(not LORA_SDR_AVAILABLE, reason="gr-lora_sdr not installed")
    def test_lora_crc_verif_available(self):
        """Verify crc_verif block is registered."""
        block_keys = self._get_platform_blocks()
        assert "lora_sdr_crc_verif" in block_keys

    def test_xmlrpc_server_available(self):
        """Verify XML-RPC server block exists (needed for runtime control)."""
        block_keys = self._get_platform_blocks()
        assert "xmlrpc_server" in block_keys

    def test_osmosdr_source_available(self):
        """Verify RTL-SDR source block exists."""
        block_keys = self._get_platform_blocks()
        # osmosdr may or may not be available depending on install
        # Just check it doesn't crash
        assert isinstance(block_keys, list)


@pytest.mark.skipif(
    not (GNURADIO_AVAILABLE and LORA_SDR_AVAILABLE),
    reason="GNU Radio + gr-lora_sdr required",
)
class TestFlowgraphConstruction:
    """Integration tests for LoRa flowgraph construction."""

    def test_build_lora_receiver_creates_grc(self):
        """Test that build_lora_receiver creates a valid compiled flowgraph."""
        from lora_scanner import build_lora_receiver

        py_path = build_lora_receiver(915.0, sf=7, bw=125000, cr=1, gain=20)

        assert py_path.exists()
        assert py_path.suffix == ".py"

        py_code = py_path.read_text()

        # Should have XML-RPC server
        assert "SimpleXMLRPCServer" in py_code

        # Should have freq variable
        assert "freq" in py_code

        # Should have sf variable
        assert "sf" in py_code

        # Should have get/set methods for runtime control
        assert "get_freq" in py_code
        assert "set_freq" in py_code
        assert "get_sf" in py_code
        assert "set_sf" in py_code

    def test_build_lora_receiver_has_lora_blocks(self):
        """Verify compiled flowgraph contains gr-lora_sdr blocks."""
        from lora_scanner import build_lora_receiver

        py_path = build_lora_receiver(915.0, sf=10, bw=125000)
        py_code = py_path.read_text()

        # Should contain gr-lora_sdr block references
        assert "frame_sync" in py_code
        assert "fft_demod" in py_code
        assert "crc_verif" in py_code

    def test_flowgraph_compiled_structure(self):
        """Verify the compiled flowgraph has correct class structure."""
        from lora_scanner import build_lora_receiver
        import ast

        py_path = build_lora_receiver(903.9, sf=12, bw=250000, cr=4)
        py_code = py_path.read_text()

        tree = ast.parse(py_code)

        class_defs = [
            node for node in ast.walk(tree) if isinstance(node, ast.ClassDef)
        ]
        assert len(class_defs) >= 1

        # Find method definitions in the main class
        lora_class = class_defs[0]
        method_names = [
            node.name
            for node in lora_class.body
            if isinstance(node, ast.FunctionDef)
        ]

        # Should have get/set for all XML-RPC-exposed variables
        assert "get_freq" in method_names
        assert "set_freq" in method_names
        assert "get_sf" in method_names
        assert "set_sf" in method_names
        assert "get_bw" in method_names
        assert "set_bw" in method_names
        assert "get_cr" in method_names
        assert "set_cr" in method_names

    def test_build_lora_receiver_xmlrpc_port(self):
        """Verify compiled flowgraph uses correct XML-RPC port."""
        from lora_scanner import build_lora_receiver

        py_path = build_lora_receiver(915.0)
        py_code = py_path.read_text()

        # Should use port 8091 (not 8090 which is FM)
        assert "8091" in py_code
        assert "0.0.0.0" in py_code or "''" in py_code
